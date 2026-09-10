#!/usr/bin/env python3
"""coverage_record.py — what a borrower's month actually covered, said out loud.

A module that produced nothing comes back with an empty results list, which reads
exactly like "there was nothing to report". So a borrower whose files we could
not find is indistinguishable from a borrower who had a clean month, and a module
that never ran at all leaves no trace but an absent file.

This walks the archive folder for one period and states, for every module the
borrower's roster enables, which of four things happened:

    reported  an output is archived and carries figures
    empty     an output is archived and carries none — read, but nothing found
    skipped   a skip record is archived; its own reason is quoted
    missing   the roster enables it and nothing was archived at all

It READS the archive rather than being told what happened, so it cannot disagree
with what is on disk. No judgment, no figures of its own: the record is the
directory listing, turned into one file a reader can answer "can we still read
this borrower?" from.

Usage:
    python coverage_record.py --archive <_archive folder> --period YYYY-MM \
                       --borrower-config <borrower-config.json> --out <path>
    python coverage_record.py --archive ... --period ... --modules a,b,c --out ...

Deterministic; stdlib only (monitor_lib for the envelope); no network.
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.abspath(os.path.join(_HERE, "..", "..", "..")), "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

import monitor_lib as ml

MODULE = "coverage"

# Everything ml.conclusion() and ml.skip_record() put on an output themselves;
# every other top-level key is the module's own content.
_ENVELOPE_KEYS = frozenset((
    "module", "period", "run_id", "run_id_from", "produced_at", "produced_by",
    "stores", "source_ref_reach", "source_files", "results", "exceptions",
    "figure_collections", "skipped", "reason", "borrower",
))


def _figure_records(node):
    """Figure records at any depth under `node`. A figure is a record carrying
    its own `source_ref` — what validate_output requires of every one — so the
    count does not depend on where a module nests its figures, and the prose and
    metadata a real output also carries (`action_items`, `model_month_ends`,
    `ytd_months`, `budget_source`) are not mistaken for them."""
    if isinstance(node, dict):
        if "source_ref" in node:
            return 1
        return sum(_figure_records(v) for k, v in node.items() if k != "source_ref")
    if isinstance(node, list):
        return sum(_figure_records(v) for v in node)
    return 0


def _figure_count(out):
    """How many figures an archived output actually carries — its `results`
    records, plus the collections it declares as holding figures, plus, for an
    output that states neither, the figures it holds under its own keys. Real
    archived outputs take that third shape whenever a module was written out by
    hand, and the figures under those keys count the same. Zero is the signal
    `empty` exists to carry, so it is counted, not inferred."""
    n = len(out.get("results") or [])
    for key in (out.get("figure_collections") or []):
        v = out.get(key)
        if isinstance(v, (list, dict)):
            n += len(v)
    if n:
        return n
    return sum(_figure_records(v) for key, v in out.items()
               if key not in _ENVELOPE_KEYS)


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _is_module_output(path):
    """Whether an archived file is a module's answer, which is what an entry in
    this record stands for.

    A module output names its own `module`. Data the run archives beside the
    outputs names none — the transcribed certified values the covenant worker
    files under `certified-values-<YYYY-MM>.json` carry the period in the name
    and nothing else, and reading one as a module puts a module on the record
    that was never on the roster. A file that will not parse counts as an
    output, so a truncated archive still gets looked at."""
    d = _read(path)
    return d is None or bool(d.get("module"))


def _when(path, d):
    """When this record was produced — its own stamp, else the file's own time.

    ISO-8601 UTC stamps compare correctly as strings; the mtime fallback is
    formatted the same way so the two are comparable.
    """
    stamp = (d or {}).get("produced_at")
    if isinstance(stamp, str) and stamp:
        return stamp
    try:
        return datetime.fromtimestamp(os.path.getmtime(path),
                                      timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return ""


def module_state(archive, module, period):
    """The one state this module reached for this period, with what says so."""
    ok = os.path.join(archive, f"{module}-{period}.json")
    skip = os.path.join(archive, f"{module}-{period}.skipped.json")

    # A month may still hold BOTH records: an engine write retires the other
    # name (monitor_lib.retire_counterpart), an archive written before that did
    # not, and a hand-written Write leaves whatever was there. So the newer
    # record is the one that says what this module reached — reading the skip
    # first would report a module skipped, and the month unreadable, forever
    # after one bad run.
    if os.path.exists(skip) and (not os.path.exists(ok)
                                 or _when(skip, _read(skip)) >= _when(ok, _read(ok))):
        d = _read(skip) or {}
        return {"module": module, "state": "skipped",
                "reason": d.get("reason") or "a skip record with no stated reason",
                "run_id": d.get("run_id"), "produced_at": d.get("produced_at"),
                "archived": os.path.basename(skip)}

    if os.path.exists(ok):
        d = _read(ok)
        if d is None:
            return {"module": module, "state": "missing",
                    "reason": "the archived output could not be read as JSON",
                    "archived": os.path.basename(ok)}
        if d.get("skipped") is True:
            return {"module": module, "state": "skipped",
                    "reason": d.get("reason") or "skipped under the normal output name",
                    "run_id": d.get("run_id"), "produced_at": d.get("produced_at"),
                    "archived": os.path.basename(ok)}
        n = _figure_count(d)
        return {"module": module, "state": "reported" if n else "empty",
                "figures": n, "exceptions": len(d.get("exceptions") or []),
                "reason": None if n else "an output was archived carrying no figures",
                "run_id": d.get("run_id"), "produced_at": d.get("produced_at"),
                "source_ref_reach": d.get("source_ref_reach"),
                "archived": os.path.basename(ok)}

    return {"module": module, "state": "missing",
            "reason": "the roster enables this module and nothing was archived "
                      "for this period", "archived": None}


def compute(archive, period, modules, borrower=None, run_id=None):
    rows = [module_state(archive, m, period) for m in modules]

    # Anything archived for this period that the roster does not list — a module
    # turned off after it ran, or a name that drifted. Reported, not dropped:
    # an output nobody expects is as much of a coverage problem as a missing one.
    listed = set(modules)
    extra, named = [], set()
    for path in sorted(glob.glob(os.path.join(archive, f"*-{period}*.json"))):
        base = os.path.basename(path)
        # Only the two names a module output can have. The glob is broad enough
        # to catch anything else that merely carries the period in its name, and
        # trimming such a file by one of these suffixes invents a module.
        for suffix in (f"-{period}.json", f"-{period}.skipped.json"):
            if base.endswith(suffix):
                name = base[:-len(suffix)]
                break
        else:
            continue
        # One entry per module, not per file: a module dropped from the roster
        # that left both an output and a skip record is one coverage problem.
        if (name and name != MODULE and name not in listed and name not in named
                and _is_module_output(path)):
            named.add(name)
            extra.append({"module": name, "state": "unlisted",
                          "reason": "archived for this period but not in the "
                                    "borrower's module roster",
                          "archived": base})

    tally = {}
    for r in rows + extra:
        tally[r["state"]] = tally.get(r["state"], 0) + 1

    return ml.conclusion(
        module=MODULE, period=period, results=[], exceptions=[],
        source_files=[], run_id=run_id, borrower=borrower,
        modules=rows + extra, tally=tally,
        readable=all(r["state"] in ("reported", "empty") for r in rows),
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", required=True,
                    help="the run's _archive folder")
    ap.add_argument("--period", required=True, help="ISO YYYY-MM")
    ap.add_argument("--borrower-config",
                    help="borrower-config.json — its modules[] is the roster")
    ap.add_argument("--modules",
                    help="comma-separated roster, instead of the borrower-config")
    ap.add_argument("--borrower")
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-id",
                    help="the run this output belongs to, minted once by the "
                         "orchestrator for the whole run (or set CREDIT_MONITOR_RUN_ID)")
    args = ap.parse_args()
    if args.run_id:
        os.environ["CREDIT_MONITOR_RUN_ID"] = args.run_id   # run_stamp() reads it

    # period_stamp raises on anything it cannot parse, so the ISO test is
    # settled here and answered below alongside the other bad-input cases — a
    # bad --period leaves a skip record, the way a missing archive folder does.
    try:
        period_ok = ml.period_stamp(args.period) == args.period
    except ValueError:
        period_ok = False

    borrower, modules = args.borrower, None
    if args.modules:
        modules = [m.strip() for m in args.modules.split(",") if m.strip()]
    elif args.borrower_config:
        cfg = ml.load_config(ml.resolve_path(args.borrower_config))
        modules = [m for m in (cfg.get("modules") or []) if isinstance(m, str)]
        borrower = borrower or cfg.get("borrower_name")
    if not modules:
        ap.error("no module roster — pass --modules or a --borrower-config "
                 "whose modules[] lists them")

    archive = ml.resolve_path(args.archive)
    if not period_ok:
        out = ml.skip_record(MODULE, ValueError(
            f"--period must be the ISO YYYY-MM stamp, not '{args.period}'"),
            borrower=borrower)
    elif not os.path.isdir(archive):
        out = ml.skip_record(MODULE, ValueError(
            f"archive folder not found at '{args.archive}'"),
            period=args.period, borrower=borrower)
    else:
        out = compute(archive, args.period, modules, borrower=borrower)

    out_path = ml.write_output(out, args.out)
    if out.get("skipped"):
        print(f"Wrote {out_path}: SKIPPED -- {out['reason']}")
    else:
        parts = ", ".join(f"{n} {state}" for state, n in sorted(out["tally"].items()))
        print(f"Wrote {out_path}: {len(out['modules'])} module(s) — {parts}; "
              f"borrower {'readable' if out['readable'] else 'NOT fully readable'}")


if __name__ == "__main__":
    main()
