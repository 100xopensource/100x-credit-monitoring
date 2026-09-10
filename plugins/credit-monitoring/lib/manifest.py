#!/usr/bin/env python3
"""Freeze a finished run folder, and check a frozen one against its manifest.

Three commands:

  finish   refuse pending work, freeze the run, then refresh the current view.
  build    write a manifest for one folder and nothing else.
  verify   re-hash a folder against its manifest and report each file.

`finish` is safe to retry: a run whose manifest still verifies is not rewritten.

Exit codes: 0 the folder is what its manifest says, 1 it is not (verify), or
the command could not be completed (build). `verify` on a folder with
no manifest exits 0 and says the run is still being written -- that is a state,
not a fault. `verify` on a folder that is not there exits 1: a name that points
at nothing is a mistake, not a run in progress.

Dependencies: Python stdlib only.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

try:
    from plugin.lib import run_lib as ml  # noqa: E402 — checkout/tests
except ModuleNotFoundError:
    import run_lib as ml  # noqa: E402 — extracted plugin script


def _report(checked, folder):
    """One line per problem, in the words the fix is in."""
    if checked["status"] == "in-progress":
        return [f"{folder} has no manifest yet — a run is still writing it."]
    if checked["status"] == "missing":
        return [f"there is no folder at {folder} — check the folder name."]
    if checked["status"] == "ok":
        return [f"{folder} matches its manifest "
                f"({len(checked['matched'])} files, run {checked['run_id']})."]
    lines = []
    for rel in checked["changed"]:
        lines.append(f"changed since the run wrote it: {rel}")
    for rel in checked["missing"]:
        lines.append(f"named by the manifest but not in the folder: {rel}")
    for rel in checked["extra"]:
        lines.append(f"in the folder but not named by the manifest: {rel}")
    for rel in checked["unreadable"]:
        lines.append(f"could not be read, so it was not checked: {rel}")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    fin = sub.add_parser("finish", help="freeze a run and refresh the current view")
    fin.add_argument("--run", required=True, help="the run folder to freeze")
    fin.add_argument("--deal", required=True, help="the borrower's deal root")
    fin.add_argument("--period", help="YYYY-MM; read from the run folder when absent")
    fin.add_argument("--run-id", help="read from the run folder name when absent")
    fin.add_argument("--model", help="workbook to copy into the run before freezing")
    fin.add_argument("--workbook", required=True,
                     help="run-relative finished workbook role path")
    fin.add_argument("--memo", required=True,
                     help="run-relative finished memo role path")
    fin.add_argument("--borrower-key")

    bld = sub.add_parser("build", help="write a manifest for one folder")
    bld.add_argument("--folder", required=True)
    bld.add_argument("--run-id", required=True)
    bld.add_argument("--period", required=True)

    ver = sub.add_parser("verify", help="check a folder against its manifest")
    ver.add_argument("--folder", required=True)
    ver.add_argument("--json", action="store_true",
                     help="print the full report as JSON")

    args = ap.parse_args(argv)

    if args.command == "verify":
        folder = ml.resolve_path(args.folder)
        checked = ml.verify_manifest(folder)
        if args.json:
            print(json.dumps(checked, indent=2))
        else:
            for line in _report(checked, folder):
                print(line)
        return 1 if checked["status"] in ("mismatch", "unreadable",
                                          "missing") else 0

    if args.command == "build":
        folder = ml.resolve_output_path(args.folder)
        try:
            manifest = ml.build_manifest(folder, args.run_id, args.period)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"manifest written for {folder}: {len(manifest['files'])} files, "
              f"run {manifest['run_id']}, plugin {manifest['plugin_version']}.")
        return 0

    try:
        done = ml.finish_run(
            args.run, args.deal, run_id=args.run_id, period=args.period,
            model_path=args.model, borrower_key=args.borrower_key,
            roles={"financial_workbook": args.workbook, "memo": args.memo,
                   "config": "config/borrower-config.json"})
    except (ValueError, ml.StoreLockError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(done, indent=2))
    return 0




if __name__ == "__main__":
    sys.exit(main())
