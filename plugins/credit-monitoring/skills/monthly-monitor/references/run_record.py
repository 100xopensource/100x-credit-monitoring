#!/usr/bin/env python3
"""Open a monthly run, record what it has done, and check what it is about to
write over.

Seven commands, in the order a run uses them:

  check  what the deal folder holds that the finish would write over:
         files that no longer hold what the last run wrote, and whether the
         run they came from is one nobody selected. Run it BEFORE the heavy
         work starts, so the analyst decides while there is still nothing to
         lose.
  edit-intake review specific changed files `check` named, bound to the
         exact digest `check` reported for each, and record them as an
         intentional edit. This is a REVIEW DECISION ONLY -- it does not by
         itself unblock accept, discard, or finish. A file not named stays
         blocking, and a further edit after intake goes stale and is
         reported as changed again, never silently carried through.
  base   which frozen run this borrower's last state is read from -- the
         latest selected month, verified, or `seed` when no month has been
         selected yet. The run resolves it ONCE, here, and hands the answer
         to `open`.
  open   resume this month's unfinished run, or start a new one: mint the run
         id, make the run folder and write `run-ledger.json` with the base it
         was given and every step pending. `--consume-edit` pins a NEW run to
         stage and consume one specific edit-intake-accepted workbook; only
         THAT run, and only while its pin still matches the live file and the
         live acceptance, may later finish over it -- no other run and no
         accept/discard is ever exempted.
  snapshot copy the complete config and stable model inputs into the run.
  step   record one step of the run as done or skipped.
  select use the frozen run as current, or keep it unpublished.
  discard discard the visible draft, naming it exactly. Restores the prior
         accepted run's files when one exists; otherwise clears the draft
         deliverables and leaves settings and model inputs untouched.

`manifest.py finish` is the other half. It refuses to freeze a run while a
step is still pending, which is what these commands exist to feed: a step
nobody did leaves no evidence of its own.

Exit codes: 0 the command was completed, 1 it was not. `check` exits 0 whether
or not it found something -- what it found is an answer, not a fault.

Dependencies: Python stdlib only.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

import run_lib as ml  # noqa: E402


def _check_lines(found):
    """The warning, in the words its fix is in. One line per file, because the
    analyst decides per file whether it is work or an accident."""
    if not found["checked"]:
        return ["this borrower has no frozen record yet, so there is nothing "
                "in the deal folder that a run would write over."]
    lines = []
    comparisons = {c["path"]: c for c in found.get("comparisons", [])}
    for rel in found["changed"]:
        c = comparisons.get(rel) or {}
        intake = c.get("intake")
        if intake:
            reason = f", {intake['reason']}" if intake.get("reason") else ""
            lines.append(
                f"changed since the last run wrote it, and already accepted as an "
                f"edit at {intake.get('at')}{reason} (digest {c.get('digest')}): {rel}")
        else:
            lines.append(
                f"changed since the last run wrote it (digest {c.get('digest')}): {rel}")
    for rel in found.get("repackaged", []):
        lines.append(f"repackaged (same cells, new container bytes): {rel}")
    for rel in found["missing"]:
        lines.append(f"the last run wrote it and it is not there now: {rel}")
    for rel in found["unreadable"]:
        lines.append(f"could not be read, so it was not checked: {rel}")
    if found["unpublished"]:
        lines.append(f"the deal folder came from run {found['run_id']}, which "
                     f"is not the run selected for {found['period']}.")
    return lines or ["the deal folder holds exactly what the last run wrote."]


def _base_of(folder, run_id, period, selected=True):
    """The `base` block a ledger records, and the verified folder behind it."""
    if not run_id:
        return {"from": "seed"}, None
    if not period:
        raise ValueError(
            "--base-run needs --base-period as well: a run id names a folder "
            "only inside its own month.")
    found = (ml.published_run if selected else ml.verified_run)(
        folder, period, run_id)
    return ({"from": "run", "run_id": found["run_id"],
             "period": found["period"]}, found["folder"])


_OLD_LAYOUT = ("this borrower is still on the old layout: it has no deal "
               "folder, so the run writes where it always did, keeps no "
               "ledger, and is not frozen.")


def _old_layout(monitor_output_folder):
    """True for a borrower not yet seeded into the Monthly Monitor store. Its
    output folder has no `.record/` above it, so there is no run folder to
    open, no ledger to record and no run to freeze."""
    return ml.deal_root_of(ml.resolve_output_path(monitor_output_folder)) is None


def _open(args):
    deal = ml.deal_root_of(ml.resolve_output_path(args.folder))
    if deal is None:
        raise ValueError("unsupported old store layout; create a new store without changing this folder")
    with ml.store_lock(deal):
        return _open_locked(args, deal)


def _open_locked(args, deal):
    folder = args.folder
    period = args.period
    if _old_layout(folder):
        here = ml.resolve_output_path(folder)
        if args.json:
            print(json.dumps({"state": "old-layout", "run_id": None,
                              "run_folder": here, "base": {"from": "seed"},
                              "base_folder": None, "pending": []}, indent=2))
        else:
            print(_OLD_LAYOUT)
            print(f"run folder: {here}")
        return 0
    resumed = ml.resumable_run(folder, period)
    if resumed and args.consume_edit:
        raise ValueError(
            "a resumed run already has its own pinned inputs; open a new run "
            "to consume a different accepted edit")
    if resumed:
        # The base comes from the ledger, not from whatever was passed in: a
        # month selected while the session was down would otherwise move the
        # base out from under a run that already computed half its answers on
        # the old one.
        run_id, run = resumed["run_id"], resumed["folder"]
        ledger = resumed["ledger"]
        recorded = ledger.get("base") or {"from": "seed"}
        base, base_folder = _base_of(folder, recorded.get("run_id"),
                                     recorded.get("period"), selected=False)
        state = "resumed"
    else:
        if args.seed_base:
            if args.base_run or args.base_period or args.draft_base:
                raise ValueError("--seed-base cannot be combined with a run base")
            base, base_folder = {"from": "seed"}, None
        elif args.draft_base:
            if not args.base_run or not args.base_period:
                raise ValueError("--draft-base needs --base-run and --base-period")
            if any(e.get("event") == "discarded" and e.get("run_id") == args.base_run
                   and e.get("period") == args.base_period
                   for e in ml._read_jsonl(ml._changelog_path(deal))):
                raise ValueError("a discarded run cannot be a draft continuation base")
            base, base_folder = _base_of(folder, args.base_run, args.base_period, selected=False)
        elif args.base_run:
            base, base_folder = _base_of(folder, args.base_run, args.base_period)
        else:
            latest = ml.latest_published(folder)
            base, base_folder = _base_of(folder, (latest or {}).get("run_id"),
                                         (latest or {}).get("period"))
        run_id = ml.mint_run_id()
        run = ml.resolve_output_path(
            ml.run_output_folder(folder, period, run_id))
        # An id collision must never reopen or rewrite a completed run.
        os.makedirs(run, exist_ok=False)
        ledger = ml.new_ledger(run_id, period, base=base,
                               analyst_focus=args.focus,
                               modules=[m.strip() for m
                                        in (args.modules or "").split(",")
                                        if m.strip()])
        ledger["delivery_base"] = ml.transaction.snapshot(deal)
        consume_edit = None
        if args.consume_edit:
            consume_edit = ml.pin_consumed_edit(deal, args.consume_edit)
            ledger["consume_edit"] = {k: consume_edit[k]
                                      for k in ("path", "digest", "at", "reason")}
        ml.write_output(ledger, ml.ledger_path(run))
        state = "started"

    consume_edit_out = ledger.get("consume_edit")
    if state == "started" and args.consume_edit:
        # Not persisted in the ledger: the filesystem source is derivable from
        # deal root + path and would otherwise be a second place to disagree.
        consume_edit_out = dict(consume_edit_out, source_path=consume_edit["source_path"])
    out = {"state": state, "run_id": run_id, "run_folder": run,
           "base": base, "base_folder": base_folder,
           "pending": ml.pending_steps(ledger),
           "analyst_focus": ledger.get("analyst_focus") or [],
           "consume_edit": consume_edit_out}
    out.update(ml.run_snapshot_state(run, ledger))
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    print(f"{state} {run_id} for {period}")
    print(f"run folder: {run}")
    if out["consume_edit"]:
        print(f"this run is pinned to consume the accepted edit at "
              f"{out['consume_edit']['path']}: pass --model "
              f"\"{out['consume_edit']['source_path']}\" to `snapshot` below.")
    print("last state comes from: "
          + (f"{base['period']} run {base['run_id']} at {base_folder}"
             if base.get("from") == "run"
             else "the deal folder as it was seeded (no selected month yet)"))
    if state == "resumed":
        print("still to do: " + (", ".join(out["pending"]) or "nothing"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    chk = sub.add_parser("check", help="what a run would write over")
    chk.add_argument("--folder", required=True,
                     help="paths.monitor_output_folder from borrower-config")
    chk.add_argument("--json", action="store_true")

    bse = sub.add_parser("base", help="which frozen run last state comes from")
    bse.add_argument("--folder", required=True,
                     help="paths.monitor_output_folder from borrower-config")
    bse.add_argument("--before-period",
                     help="YYYY-MM; take the latest selected month BEFORE this "
                          "one, which is what a revision of a selected month "
                          "reads its continuity inputs from")
    bse.add_argument("--json", action="store_true")

    opn = sub.add_parser("open", help="resume or start this month's run")
    opn.add_argument("--folder", required=True,
                     help="paths.monitor_output_folder from borrower-config")
    opn.add_argument("--period", required=True, help="YYYY-MM")
    opn.add_argument("--base-run", help="the run id `base` printed; leave it "
                                        "out when `base` said seed")
    opn.add_argument("--base-period", help="the month that run belongs to")
    opn.add_argument("--seed-base", action="store_true",
                     help="explicitly use setup inputs when the historical base lookup returned seed")
    opn.add_argument("--draft-base", action="store_true",
                     help="explicitly continue the named verified, non-discarded draft")
    opn.add_argument("--modules", help="the borrower's roster, comma-separated")
    opn.add_argument("--focus", action="append", default=[],
                     help="one analyst focus item; repeat for more")
    opn.add_argument("--consume-edit",
                     help="a financial workbook already accepted via edit-intake, "
                          "named exactly as edit-intake and check report it; pins "
                          "this new run to stage and consume exactly that edit")
    opn.add_argument("--json", action="store_true")

    snap = sub.add_parser("snapshot", help="copy config and stable model inputs into a run")
    snap.add_argument("--run", required=True, help="the active run folder")
    snap.add_argument("--config", required=True, help="borrower-config.json")
    snap.add_argument("--model", required=True, help="the stable model workbook")

    stp = sub.add_parser("step", help="record one step as done or skipped")
    stp.add_argument("--run", required=True, help="the run folder")
    stp.add_argument("--step", required=True,
                     help="one of: " + ", ".join(ml.LEDGER_STEPS))
    stp.add_argument("--module", help="the module, when the step is modules")
    stp.add_argument("--state", default="done", choices=list(ml.LEDGER_STATES))

    sel = sub.add_parser("select", help="use this frozen run as the current result")
    sel.add_argument("--run", required=True, help="the frozen run folder")
    sel.add_argument("--deal", required=True, help="the borrower's deal root")
    sel.add_argument("--period", help="YYYY-MM; read from the manifest when absent")
    sel.add_argument("--keep-unpublished", action="store_true")

    dis = sub.add_parser("discard", help="discard the visible draft, naming it exactly")
    dis.add_argument("--deal", required=True, help="the borrower's deal root")
    dis.add_argument("--run-id", required=True,
                     help="the exact run id being discarded, read from the draft's manifest")
    dis.add_argument("--period", help="YYYY-MM; read from the manifest when absent")
    dis.add_argument("--reason", help="why the analyst is discarding this draft")

    edt = sub.add_parser(
        "edit-intake",
        help="review specific changed deliverables and record them as an "
             "intentional edit -- a decision only, not a delivery exemption")
    edt.add_argument("--deal", required=True, help="the borrower's deal root")
    edt.add_argument("--edit", dest="edits", nargs=2, metavar=("FILE", "DIGEST"),
                     action="append", required=True,
                     help="a file named exactly as `check`'s `comparisons` "
                          "reports it (its `path`), and the exact `digest` it "
                          "reported for that same entry; repeat for more than one")
    edt.add_argument("--reason", help="why the analyst is accepting this edit")
    edt.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    try:
        if args.command == "check":
            found = ml.unapproved_deal_work(args.folder)
            if args.json:
                print(json.dumps(found, indent=2))
            else:
                for line in _check_lines(found):
                    print(line)
            return 0
        if args.command == "base":
            found = None if _old_layout(args.folder) else ml.latest_published(
                args.folder, before_period=args.before_period)
            if args.json:
                print(json.dumps(found or {"from": "seed"}, indent=2))
            elif found:
                print(f"{found['period']} {found['run_id']}")
                print(found["folder"])
            else:
                print("seed")
                print("no month has been selected for this borrower yet, so "
                      "last state is the deal folder as it was seeded.")
            return 0
        if args.command == "open":
            return _open(args)
        if args.command == "snapshot":
            print(json.dumps(ml.snapshot_run_inputs(
                args.run, args.config, args.model), indent=2))
            return 0
        if args.command == "select":
            result = ml.select_current_result(
                args.run, args.deal, period=args.period,
                keep_unpublished=args.keep_unpublished)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "discard":
            result = ml.discard_draft(
                args.deal, args.run_id, period=args.period, reason=args.reason)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "edit-intake":
            result = ml.record_edit_intake(args.deal, args.edits, reason=args.reason)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print("accepted as an edit (pending a run to consume it): "
                      + ", ".join(result["accepted"]))
                print("still blocking any accept, discard, or unrelated finish: "
                      + (", ".join(result["still_blocking"]) or "nothing"))
            return 0
        run = ml.resolve_path(args.run)
        if ml.read_ledger(run) is None and _old_layout(args.run):
            print(_OLD_LAYOUT)
            return 0
        ledger = ml.mark_step(run, args.step, args.state, module=args.module)
        waiting = ml.pending_steps(ledger)
        print(f"{args.module or args.step}: {args.state}. "
              + ("still to do: " + ", ".join(waiting) if waiting
                 else "every step is done — the run can be frozen."))
        return 0
    except (ValueError, OSError, ml.StoreLockError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
