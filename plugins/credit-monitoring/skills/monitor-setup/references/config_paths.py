#!/usr/bin/env python3
"""config_paths.py — turn a picked file into what a borrower-config records, and
hold a finished config to it.

A borrower-config must not record the machine path used during setup: that path
will not resolve for another reader or after a temporary session ends.

A configured path names the SHARED FOLDER and the path inside it:

    store:Example Borrower/.record/current/_model/covenant-spec.json
    source:Example Borrower/6_Loan Monitoring/1_Reporting

which is the same string for everybody, and resolves against whatever the
reader has connected — including when they connected a folder ABOVE it.

Two commands:

    name   <local path> [...]   what to write in the config for a file you picked
    check  <borrower-config.json>   every path in it that still names a machine

Usage:
    python config_paths.py name "/sessions/<id>/mnt/<folder>/Example Borrower/_model/spec.json"
    python config_paths.py check "<the borrower-config.json you just wrote>"

Both print JSON to stdout. `check` exits 1 when a path still names a machine, so
setup cannot finish on a config that only works where it was written.

Deterministic; stdlib only (monitor_lib for the folder catalog); no network.
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.abspath(os.path.join(_HERE, "..", "..", "..")), "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

import monitor_lib as ml  # noqa: E402


def name_for(local_path):
    """What a config should record for a file at `local_path`, as a dict:
    {"given", "configured", "reason"}. `configured` is None when the file is in
    no shared folder we know — the caller then asks the analyst to put it in one
    rather than writing a path only they can open."""
    given = str(local_path)
    said = ml.unresolve_path(given)
    if ml.parse_store_path(said):
        return {"given": given, "configured": said, "reason": None}
    return {"given": given, "configured": None,
            "reason": ("this file is not in any of the shared folders I know "
                       f"({', '.join(sorted(ml.connected_stores())) or 'none connected'}), "
                       "so a path to it would only work on this computer. Please "
                       "put it in the deal folder or the monitoring folder.")}


def _walk(node, trail, out):
    """Every string value under `node`, with the key path that reaches it.
    Commentary keys ('_'-prefixed) are prose about the config, not paths."""
    if isinstance(node, dict):
        for k, v in node.items():
            if not str(k).startswith("_"):
                _walk(v, trail + [str(k)], out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, trail + [str(i)], out)
    elif isinstance(node, str):
        out.append((".".join(trail), node))


# A value that only works where it was written. `_MOUNT_GLOB`'s sessions form, a
# Windows drive or share, and a home directory are all machine-specific.
def _names_a_machine(value):
    v = str(value)
    try:
        if ml.parse_store_path(v):
            return False
    except ml.ConfigError:
        return True
    low = v.replace("\\", "/").lower()
    return (low.startswith("/sessions/")
            or (len(v) > 1 and v[1] == ":" and v[:1].isalpha())
            or low.startswith("//")
            or low.startswith("/home/") or low.startswith("/users/")
            or "/onedrive" in low)


# The key suffixes a config records a path under ('..._path', '..._folder'). The
# rest of the file is prose — 'budget_version_note' and 'analysis_notes' sit
# beside the paths and may well mention a folder — and prose is not something a
# reader resolves, so gating on it would block setup over commentary.
_PATH_KEY_SUFFIXES = ("_path", "_paths", "_folder", "_folders")


def _is_path_key(key):
    """True when the key names a path. List indices ('sources.0') are not the
    name, so read past them to the key that is."""
    named = [part for part in key.split(".") if not part.isdigit()]
    return bool(named) and named[-1].endswith(_PATH_KEY_SUFFIXES)


def check(config_path):
    """Every configured path in a borrower-config that still names a machine, as
    {"config", "ok", "problems": [{"key", "value"}]}."""
    with open(ml.resolve_path(config_path), "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    found = []
    _walk(cfg, [], found)
    problems = [{"key": k, "value": v} for k, v in found
                if _is_path_key(k) and _names_a_machine(v)]
    return {"config": str(config_path), "ok": not problems, "problems": problems}


def _cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("name", help="what to record for a file you picked")
    n.add_argument("path", nargs="+")
    c = sub.add_parser("check", help="paths in a borrower-config that name a machine")
    c.add_argument("config")
    a = ap.parse_args(argv)

    if a.cmd == "name":
        out = [name_for(p) for p in a.path]
        print(json.dumps(out if len(out) > 1 else out[0], indent=2, ensure_ascii=False))
        return 0 if all(r["configured"] for r in out) else 1
    result = check(a.config)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(_cli())
