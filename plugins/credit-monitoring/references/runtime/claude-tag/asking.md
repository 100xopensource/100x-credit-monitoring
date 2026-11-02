# Asking in a Claude Tag channel

A channel has no menu tool. The thread session says everything the analyst reads, through the
`mcp__slackbot__*` tools: `reply` posts in the thread, `react` and `unreact` mark a message, and
`set_thread_label` names the stage beside Claude's name. A worker holds only the read-only
`mcp__slackbot_read__*` tools: it can read the thread, but it cannot post or react. A worker returns
what it found, and any question, to the thread session, which says it.

## The request is the go-ahead

The analyst's request starts the run. It replaces the welcome and the continue-or-stop menu in
`../../entry-routing.md`, and **Saying hello, and what this is** and **Saving it, or stopping before
saving** in `../../asking.md`. Send no welcome and no continue-or-stop question.

- **The request settles the edit.** A request that names a change the analyst made in the workbook
  answers both edit questions in **monthly-monitor**'s **Protect current work and establish accepted
  history**: the edit is intentional, and the request's own words for why are the
  `edit-intake --reason`, such as "Talvorn sent a corrected June P&L".
- **The run keeps the earlier focus.** In place of the menu in **Settle what to watch this run**, run
  `run_record.py base --before-period <the month after the review month> --json` and keep the
  `analyst_focus` in `run-ledger.json` inside the `folder` it returns. `base` returns the newest
  selected month before the one it is given: the month's own review when the month is done again,
  and the review before it for a new month. Pass each item to `open` as `--focus`, and name it in the
  **Change found** post. With `from: seed`, or an empty `analyst_focus`, no focus is on file and the
  run reads the numbers against the deal context.
- **No forward-stress scenarios are confirmed.** That menu's scenario choice is not asked here. The
  memo's forward stress shows the computed room on each test and the move that breaks it, with no
  confirmed scenarios.
- **Any other question is asked in the thread.** Where a skill or `../../entry-routing.md` would put
  a question to the analyst that this file does not settle, ask it as **Asking a question** says.

## The only change

Before any run write, compare `check --json` with the request. The run goes on only when:

- `changed` lists the financial workbook alone, or nothing when the request names no change;
- `missing` and `unreadable` are empty;
- the workbook's `moved` lists each change the request named, on the same line and month with the
  same new figure, and nothing more.

A file under `repackaged` has the same cells as the last review. Go on, and leave it out of every
post. When `look_unchecked` lists the workbook, or its comparison has an `unread` beside the moved
cells, its notes and formats were not checked: go on, and say so in **Change found**. When its
comparison has a `look` beside the moved cells, the analyst saved a format change too, such as a
cell's colour that Excel Online replaces on a paste. Go on, keep the workbook as saved, and say so
in **Change found**.

When `unpublished` is true and the rest holds, ask **A draft nobody chose** first, and go on as
its answer says. When the run goes on and the request names a change, run `edit-intake` next. When
`check` shows anything else, or `edit-intake` refuses, post **Stopped** and stop.

## A draft nobody chose

`unpublished` is true when the workbook and memo in the borrower's folder come from a run nobody
chose as the month's review: its question in `delivery.md` got ❌, or nobody answered it. A new run
starts from those files, so ask before any run write, as **Asking a question** says. `{Month}` is
the `period` that `check` reports. For `{date}`, run `run_record.py base --before-period <the month
after {Month}> --json`: when the `period` it returns is {Month}, `{date}` is the date in its
`run_id`, and otherwise no review is chosen for {Month}. For `{restored}`, run `run_record.py base
--json` with no `--before-period`. It returns the review a discard puts back, the newest month's:
`{restored}` is the date in its `run_id` when its `period` is {Month}, and otherwise its month, such
as "May".

```text
This decides whether I build on the {Month} draft in {Borrower}'s folder, which isn't {Month}'s review.

> **Q{n}. How do I go on from the {Month} draft?**
>
> 1. Build on the draft. The {date} review stays {Month}'s review until you choose another.
> 2. Make the draft {Month}'s review, then go on.
> 3. Discard the draft and put back the {restored} review, then go on.
> 4. Stop here.

_My pick is 1._ It leaves {Month}'s review as it is, and this run ends by asking again.
```

When no review is chosen for {Month}, option 1 is "Build on the draft. {Month} stays without a
review until you choose one." Leave out the options the plugin would refuse, and number the rest
in order:

- When the request names a change, leave out options 2 and 3. The plugin won't choose or discard a
  review while the workbook holds a change no run has taken in, and the question at the end of this
  run offers the choice with the change in.
- When `base` with no `--before-period` returns `from: seed`, leave out option 3. No month has a
  review to put back, and the plugin won't discard a borrower's first review.

On the answer, set the request's reaction to ⏳.

- **Build on the draft.** The label stage goes back to `reading the folder`. Go on as **The only
  change** says. {Month}'s review stays as it is until the question in `delivery.md`.
- **Make the draft {Month}'s review.** The label stage is `updating the dashboard`. One worker runs
  `select` on the draft, its folder named by `check`'s `period` and `run_id`, then does steps 2 and
  3 of **Yes** in `delivery.md`:

  ```bash
  python "$CLAUDE_PLUGIN_ROOT/skills/monthly-monitor/references/run_record.py" select \
    --run "$W/store/borrowers/<borrower key>/.record/runs/<period>/<run_id>" \
    --deal "$W/store/borrowers/<borrower key>" --period "<period>"
  ```

  Post "The {Month} draft is now {Borrower}'s {Month} review. The dashboard shows it the next time
  it's opened." Then set the label stage to `reading the folder`, run `check` again, and go on as
  **The only change** says.
- **Discard the draft.** The label stage is `updating the dashboard`. One worker runs `discard` on
  the draft, named by `check`'s `run_id` and `period`, then does steps 2 and 3 of **Yes** in
  `delivery.md`:

  ```bash
  python "$CLAUDE_PLUGIN_ROOT/skills/monthly-monitor/references/run_record.py" discard \
    --deal "$W/store/borrowers/<borrower key>" --run-id "<run_id>" --period "<period>"
  ```

  The discard puts the {restored} review's workbook and memo back in the borrower's folder and
  keeps the draft's run on record. Post "I discarded the {Month} draft. {Borrower}'s
  folder holds the {restored} review again." Then set the label stage to `reading the folder`, run
  `check` again, and go on as **The only change** says.
- **Stop here.** Post **Stopped**.

A refused `select` or `discard`, or a failed save, gets the words for it in `files.md`, or the
plugin's own reason, and the reaction ❌.

## Asking a question

A question goes up as soon as its evidence is in, and the work carries on with every step the answer
does not gate. A worker returns the question with its findings, and the thread session posts it. On
the answer, carry those findings forward and recompute nothing.

Read the thread before you post. Number questions Q1, Q2, and so on in each thread: the next number
is the highest the thread holds plus one. Post one question per message, and keep at most four open.
A question is one plain sentence on what the pick changes, then a blockquote holding the question in
bold with its number and two to four options as a numbered list, then our pick and its evidence in
one sentence:

```text
This decides which June package Talvorn's review reads.

> **Q2. Which June package do I read?**
>
> 1. The package saved 12 July
> 2. The copy saved 3 July
> 3. Neither, and I stop here

_My pick is 1._ It holds the signed compliance certificate.
```

Option 1 is our pick. A credit judgment carries no pick (`../../asking.md`). A yes-or-no question has
two options, Yes first. Start each option at the beginning of its line: Slack turns four leading
spaces into a code block. Tag the person who asked on the thread's first question, built from the
user id on their request as `<@USER_ID>`, since a typed name notifies nobody; tag nobody after that.
Post a question with `review: false` and a `justification` saying it is a question the analyst
answers by reacting on it.

Right after posting, react the menu under the question, one reaction per call, in order: `one`,
`two`, `three`, `four` for the options it has, or `white_check_mark` then `x` for a yes-or-no, and
`question` last. Read the thread back, and when the menu is missing a reaction or is out of order,
remove it and react it again in order.

- **The answer.** A person's number, `white_check_mark` or `x` on the question is the answer. A
  reaction from Claude's own account is the menu being set, never an answer. A typed reply that names
  a question's number (`Q2: 1`, `2 on Q3`) or an option's words answers that question. One that names
  none and fits exactly one open question answers it; ask any other typed reply which question it is
  for.
- **Say it again.** A person's `question` asks for the question again, and it is never an answer.
  Reply in simpler words and in more detail: what is being decided and why it needs them, and what
  each option means in practice. Keep the number and the menu, and post no new question.
- **Before acting.** Re-read the thread. Two people's answers on one question, or a reaction that was
  removed, means asking it again. Once the answer stands, remove the menu reactions you set, so only
  the person's pick stays.

## The status on the request

The request carries one status reaction, and the thread label reads `<Borrower> <Month> · <stage>`,
such as `Talvorn June · checking the numbers`. Call `set_thread_label` at every stage. Change the
reaction only when the stage's reaction differs from the one on the request: `unreact` the old one,
then `react` the new one. While a question gates the next step, the reaction is ✋ and the label
stage is `waiting on Q<n>`; when the answer lets the work go on, both return to the stage's own.

| Reaction | Means |
|---|---|
| 👋 `wave` | Seen. React as soon as the request arrives, with no post and no label |
| ⏳ `hourglass_flowing_sand` | Working |
| ✋ `raised_hand` | Waiting on the analyst |
| 👍 `+1` | Done |
| ❌ `x` | Stopped, with the analyst's files unchanged |

## Posting at each stage

Say the posts in this file as written and fill in the blanks. Everything else the analyst reads
follows `../../asking.md`. Post every message as a new `reply` with `review: false`, so the channel's
reviewer neither rewrites it nor turns it into an edit of the one before.

These posts replace **Before the long quiet**, **While working** and **When you stop before saving**
in `../../asking.md`. **When something is missing but the work can carry on** and **When something
goes wrong** still apply.

| Stage | When | Reaction | Label stage | Post |
|---|---|---|---|---|
| Started | Before `check` runs | ⏳ | reading the folder | **Started** |
| Change found | `check` passes and `edit-intake` takes any change; before the modules | ⏳ | checking the numbers | **Change found** |
| Checks done | Every module has returned | ⏳ | writing the memo | **Checks done** |
| Saving | The memo is written | ⏳ | saving | **Saving** |
| Waiting | A question gates the next step | ✋ | waiting on Q<n> | The question |
| Stopped | **The only change** stops the run, or the answer to **A draft nobody chose** is **Stop here** | ❌ | stopped | **Stopped** |

After **Saving**, `delivery.md` takes the run to the end on the same status.

Spell every count out as a word, such as "four checks".

**Started**. `{N}` is the number of modules in `borrower-config.modules[]`. `{minutes}` is about 30
for a month done again with an edit, and about 45 for a new month.

> "I'm starting {Borrower}'s {Month} review again with your corrected {line}. First I'll read
> {Borrower}'s folder and your change, then run {N} checks on their numbers, write the memo and save
> it. This takes about {minutes} minutes. I'll post here at each step, so there's no need to watch."

With no change in the request, the first two sentences are "I'm starting {Borrower}'s {Month}
review. First I'll read {Borrower}'s folder, then run {N} checks on their numbers, write the memo
and save it."

**Change found**. `{Focus month}` is the month whose review recorded the focus. Write `{was}` and
`{now}` as the workbook shows them, such as $7,620,004.

> "Your change is the only one in the workbook: {Month} {line}, {was} → {now}. I'm keeping
> {Focus month}'s focus, {the focus}. Reading through their numbers now."

When the request named several changes, the first sentence is "Your changes are the only ones in
the workbook:" and then each change in the same form, joined by "and". With no change in the
request, the first sentence is "Nothing in {Borrower}'s workbook has changed since the last review."
With no focus on file, the second is "There's no focus on file, so I'm reading their numbers against
the deal as a whole." When the workbook's notes and formats were not checked, add after the first
sentence: "I could check the workbook's numbers but not its notes and formats, so a change there
wouldn't show here." When the comparison has a `look`, the first sentence says "to the workbook's
figures" in place of "in the workbook", and after it add "You also changed {look.what}, and I'm
keeping it as you saved it."

**Checks done**. `{N}` is the number of modules recorded `done`.

> "All {N} checks are done. Writing the memo now."

**Saving**:

> "The memo is written. Saving the memo and workbook to {Borrower}'s folder."

**Stopped**. Give the plugin's own reason and add no cause of your own. Leave out paths, digests and
run ids. Every case says "I stopped without changing your files."

When `check` found something the request did not name, name each changed cell by its `what`, `was`
and `now`, a look change by `look.what`, and a `missing` or `unreadable` file by its file name:

> "I found something you didn't mention: {what `check` found}. I stopped without changing your
> files. If that's meant too, ask again and name it."

When the workbook's comparison has an `unread` and `moved` is empty, nothing inside it could be
compared. Give the `unread`:

> "I couldn't read {Borrower}'s workbook: {the `unread`}. I stopped without changing your files."

When the workbook read, and `check` shows no change the request named and nothing else:

> "I couldn't find your change in {Borrower}'s workbook. I stopped without changing your files."

When the answer to **A draft nobody chose** is **Stop here**:

> "The {Month} draft stays in {Borrower}'s folder as it is. I stopped without changing your files."

When `edit-intake` refuses, give the reason it prints:

> "I can't use your edited workbook: {the reason `edit-intake` gave}. I stopped without changing
> your files."
