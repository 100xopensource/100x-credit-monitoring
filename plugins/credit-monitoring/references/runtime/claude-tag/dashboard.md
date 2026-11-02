# The portfolio dashboard in a Claude Tag channel

The dashboard has one page and one link. The published page reads the dashboard folder in
SharePoint each time someone opens it, so a run writes the files the page reads and publishes
nothing.

The page belongs to a person, who publishes it once from their own Claude account and records its
link in the store. A channel never publishes it: a page a channel publishes belongs to the channel's
agent and is deleted with the channel.

## Keep it current

Once the analyst chooses a run (`delivery.md`, or a draft before a run in `asking.md`) or discards
a draft (`asking.md`), and only then, the worker that ran `select` or `discard` refreshes the
borrower's row. After `select`, it first checks that the answer carries
`required_next_step: refresh-dashboard`. It refreshes the row in the work area, as **Refresh one
selected borrower** in **refresh-dashboard** says, with `$W/store` as the `credit-monitoring-output`
folder: the build with `--only` and `--keep-published`, the shell, then `--check`. Stop on any
problem it reports.

The save after it carries `artifacts/portfolio` with the borrower's folder (`files.md`). The
dashboard's `index.json` saves last, so the page never reads a row that names pages not saved yet.
Every other borrower's row stays as it was.

Every thread saves into the same `index.json`. When the save stops at a file under
`artifacts/portfolio` because it was saved in SharePoint since this run read it, another thread
changed the dashboard first. Bring the dashboard down again, refresh the borrower's row again, and
run the same save:

```bash
SP --folder "<monitoring folder>" download --into "$W/store" --record "$W/read.json" \
  artifacts/portfolio/Artifact
```

The third stop ends the step. Then say: "{Borrower}'s review is saved, but the dashboard kept
changing while I added its row, so the row isn't on it yet."

## The link

```bash
python "$CLAUDE_PLUGIN_ROOT/skills/portfolio-artifact/references/hosted_artifact.py" status \
  --state "$W/store/artifacts/portfolio/hosted-artifact.json"
```

- `linked`: its `artifact_url` is the dashboard. Give that link.
- `create` or `recover`: no page is recorded for this store. Publish nothing. In place of the link,
  say "The dashboard isn't set up for these folders yet, so there's no link to share."

The page is private. Whoever opens it needs Claude's Microsoft 365 connector on, with access to
the monitoring folder. Change no sharing yourself: the page's owner shares it from Claude.

## What the channel never does

- Never publish or republish the page. A new run reaches it through the files.
- Never change how the page looks: layout and style belong to the page's owner, from their own
  computer. Say so when asked.
- Never refresh the whole book from a channel. A review refreshes its own borrower's row.
