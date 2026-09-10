#!/usr/bin/env python3
"""preflight.py — one plain verdict for "can I actually use this folder/file?"

A prose skill runs `python <plugin>/lib/preflight.py "<path>"` before it reads or
writes a store, so every access failure routes through ONE shared check
(monitor_lib.classify_store_access) instead of the agent improvising a
workaround. Prints a one-line JSON verdict and sets an exit code to branch on:

    0  ok               the path is connected and readable — proceed
    3  not_connected    the folder isn't connected — send the connect steps + screenshot
    4  cloud_only_stub  the file is cloud-only — tell them the keep-on-device one-liner
    5  missing          the folder IS connected but this item isn't in it — say
                        what's missing by name and treat it as a missing input
                        (skip with a plain reason); the connect steps don't apply
    2  usage error      no path given / bad args

(The remedies are spelled out in references/connecting-folders.md.)

The JSON always carries {verdict, reason, guide, path}. This tool never writes
anything and never reads file contents — it only checks whether the path is
reachable and hydrated.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import monitor_lib as ml  # noqa: E402

_EXIT = {"ok": 0, "not_connected": 3, "cloud_only_stub": 4, "missing": 5}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Check whether a store path is usable (connected + hydrated).")
    ap.add_argument("path", help="the file or folder path to check (as configured)")
    args = ap.parse_args(argv)
    verdict = ml.classify_store_access(args.path)
    print(json.dumps(verdict))
    return _EXIT.get(verdict["verdict"], 1)


if __name__ == "__main__":
    sys.exit(main())
