# Delivering in a Claude Tag channel

A run ends in the thread with one delivery post that carries both files and their links, then one
question: whether this run becomes the borrower's review for the month. A worker saves; the thread
session posts. The request's status reaction and the thread label follow `asking.md`, and every
post goes up as a new `reply` with `review: false`.

This file replaces **Step 6 — Current-result choice, then close** in **monthly-monitor**: its
three-way menu and its closing menu. Discarding is not offered here: a run kept as a draft stays in
the borrower's folder until the analyst chooses it, a later run replaces it, or the next request
for the borrower discards it (**A draft nobody chose** in `asking.md`).

## Save, then deliver

After **Saving** (`asking.md`), the worker that ran `manifest.py finish` saves the borrower's folder
as **Save the results back** in `files.md` says. It returns:

- the memo's and the workbook's `web_url`, from the listing **Save the results back** in
  `files.md` takes after the save;
- the one or two headline figures the run moved most, each with its value in the review this run
  redoes, read from this run's results and that review's;
- the date of the review currently chosen for the month, from its run id, or that none is chosen.

Nothing is chosen yet: the run is a draft. When the save fails, post the words `files.md` gives for
that failure, set the request's reaction to ❌, and stop.

## The delivery post

Post it with Slack's `upload_file`, both files in one call, the post as its `initial_comment`.
`upload_file` answers with the command that sends the files; a worker runs it from the work area.

> "{Borrower}'s {Month} review is redone with your corrected {line}, {now}. {The headline figures,
> each against budget or the earlier review.}
>
> Both files are saved in {Borrower}'s folder: <{memo web_url}|Monitoring memo> ·
> <{workbook web_url}|{the workbook's name without its extension}>"

With no change in the request, the first sentence is "{Borrower}'s {Month} review is done." Write
each link as Slack's `<url|words>`. When a save did not read back, leave that file's link out, say
"{file name} didn't save to {Borrower}'s folder", and still attach the file.

Use "is redone" or "is done" only when both files passed their checks and are attached. Otherwise
say what is missing, as **When something goes wrong** in `../../asking.md` says.

## The question after it

Right after the delivery post, ask one yes-or-no question as **Asking a question** in `asking.md`
says: its number, the menu reacted under it, and the request's reaction ✋ with the label stage
`waiting on Q<n>`.

```text
This decides which {Month} review {Borrower}'s dashboard row shows and next month's review builds on.

> **Q{n}. Make this {Borrower}'s {Month} review from now on?**
>
> 1. Yes. The dashboard shows it the next time it's opened.
> 2. No. The {date} review stays, and this one is kept as a draft.

_My pick is 1._ It's the same {Month} review with only your correction applied.
```

If the analyst asked to keep the earlier review current, preserve that instruction here: put
**Keep the {date} review current; keep this one as a draft** first and recommend it, then offer
**Replace the {date} review with this one**. Map the answer by its action, not the ordinary option
number below. Only an explicit intentional replacement answer authorizes `select` without
`--keep-unpublished`; saving the draft, silence or a general instruction to continue does not.
Carry that instruction through worker handoffs. Do not restore or change a current selection on
your own.

When no review is chosen for the month yet, option 2 is "No. {Month} stays without a review, and
this one is kept as a draft." and the reason after the pick is "It's {Borrower}'s first {Month}
review." With no change in the request, the reason is "It reads the latest figures in
{Borrower}'s folder."

## The answer

Read the answer as `asking.md` says, then set the request's reaction to ⏳.

**Explicit replacement / Yes** (ordinary menu `1` or ✅; use the replacement action in a reordered keep-current menu). The label stage is `updating the dashboard`. One worker:

1. chooses the run:

   ```bash
   python "$CLAUDE_PLUGIN_ROOT/skills/monthly-monitor/references/run_record.py" select \
     --run "<run folder>" --deal "$W/store/borrowers/<borrower key>" --period "<YYYY-MM>"
   ```

2. refreshes the borrower's dashboard row, as **Keep it current** in `dashboard.md` says;
3. saves the borrower's folder and `artifacts/portfolio` in one save (`files.md`);
4. reads the dashboard's link, as **The link** in `dashboard.md` says.

Then post, and set the request's reaction to 👍 with the label stage `done`:

> "This is now {Borrower}'s {Month} review. The dashboard shows it the next time it's opened:
> <{dashboard link}|Portfolio dashboard>"

When `dashboard.md` gives no link, the second sentence is the one it gives in place of the link.

**Keep the earlier review / No** (ordinary menu `2` or ❌; use the keep action in a reordered keep-current menu). One worker runs the same `select` with `--keep-unpublished` and saves the
borrower's folder. Post, then set 👍 and `done`:

> "The {date} review stays {Borrower}'s {Month} review. This one is kept as a draft in their
> folder. The next time you ask me about {Borrower}, I'll first ask how to go on from it."

When no review was chosen for the month, the first sentence is "{Month} stays without a review for
{Borrower}." The next request finds the draft and asks **A draft nobody chose** in `asking.md`.

A worker that finds the work area gone brings the borrower's files down again first (`files.md`).
A refused `select` or a failed save gets the words for it in `files.md`, or the plugin's own reason,
and the reaction ❌.
