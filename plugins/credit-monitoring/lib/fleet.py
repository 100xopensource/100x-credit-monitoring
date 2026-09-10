#!/usr/bin/env python3
"""Borrower registry for credit monitoring."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

try:
    from plugin.lib import run_lib
except ModuleNotFoundError:
    import run_lib

resolve_path = run_lib.resolve_path
unresolve_path = run_lib.unresolve_path


FLEET_FILENAME = "credit-monitoring-registry.json"
PUBLISHED_FILENAME = "published.json"
BORROWERS_DIR = "borrowers"
_RECORD_DIR = ".record"
_RUNS_DIR = "runs"
_STOP = {"inc", "llc", "lp", "ltd", "the", "co", "corp", "company", "holdings",
         "group", "monitoring", "consolidated", "incl"}


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _tokens(s):
    return [t for t in re.split(r"[^a-z0-9]+", str(s or "").lower()) if t]


def _candidates(entry):
    cands = [entry.get("borrower_key", ""), entry.get("name", ""),
             entry.get("folder", ""), entry.get("borrower_name", "")]
    cands += list(entry.get("aliases") or [])
    return [c for c in cands if str(c).strip()]


def resolve(query, fleet):
    borrowers = (fleet or {}).get("borrowers") or []
    qn = _norm(query)
    qtok = set(_tokens(query))
    qtok_core = (qtok - _STOP) or qtok
    exact, token = [], []
    for e in borrowers:
        cands = _candidates(e)
        if qn and qn in {_norm(c) for c in cands}:
            exact.append(e)
            continue
        ctok = set()
        for c in cands:
            ctok |= set(_tokens(c))
        if qtok_core and qtok_core <= ctok:
            token.append(e)
    hits = exact or token
    if len(hits) == 1:
        return {"status": "found", "matches": hits}
    if len(hits) > 1:
        return {"status": "ambiguous", "matches": hits}
    return {"status": "not_found", "matches": []}


def find_fleet_path(explicit=None):
    if explicit:
        p = resolve_path(explicit)
        return p if os.path.exists(p) else None
    root = os.environ.get("CREDIT_MONITOR_STORE_ROOT")
    if root:
        candidate = os.path.join(resolve_path(root), FLEET_FILENAME)
        if os.path.isfile(candidate):
            return candidate
    return None


def load_fleet(path=None):
    p = find_fleet_path(path)
    if not p:
        return None, None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh), p


def save_fleet(path, fleet):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(fleet, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _borrower_key_from_folder(folder_name):
    key = str(folder_name or "").strip()
    if (not key or key in (".", "..") or "/" in key or "\\" in key or ":" in key):
        raise ValueError("borrower key must be one folder name")
    return key


def _entry_from_folder(root, folder_name):
    borrower_key = _borrower_key_from_folder(folder_name)
    config_rel = f"{BORROWERS_DIR}/{folder_name}/.record/current/config/borrower-config.json"
    config_abs = os.path.join(root, BORROWERS_DIR, folder_name,
                              ".record", "current", "config",
                              "borrower-config.json")
    borrower_name = ""
    try:
        with open(resolve_path(config_abs), encoding="utf-8") as fh:
            borrower_name = str(json.load(fh).get("borrower_name") or "").strip()
    except Exception:
        pass
    aliases = []
    if borrower_name and _norm(borrower_name) != _norm(borrower_key):
        aliases.append(borrower_name)
    return {
        "borrower_key": borrower_key,
        "name": borrower_name or borrower_key,
        "folder": borrower_key,
        "borrower_name": borrower_name,
        "aliases": aliases,
        "config_file": config_rel,
        "onboarded": date.today().isoformat(),
    }


def bootstrap(root):
    root_r = resolve_path(root)
    borrowers_root = os.path.join(root_r, BORROWERS_DIR)
    borrowers = []
    if os.path.isdir(borrowers_root):
        for name in sorted(os.listdir(borrowers_root)):
            full = os.path.join(borrowers_root, name)
            if os.path.isdir(full) and os.path.exists(
                os.path.join(full, ".record", "current", "config", "borrower-config.json")
            ):
                borrowers.append(_entry_from_folder(root_r, name))
    return {
        "_comment": ("credit monitoring borrower registry. run-monitor reads this "
                     "ONE file to decide instantly whether a borrower is set up."),
        "borrowers": borrowers,
    }


def _config_folder(config_path):
    config_abs = resolve_path(config_path)
    folder_abs = Path(config_abs).resolve().parents[4]
    return str(folder_abs)


def register(config_path, root=None, aliases=None, borrower_key=None):
    config_abs = resolve_path(config_path)
    folder_abs = Path(config_abs).resolve().parent.parent.parent.parent
    key = _borrower_key_from_folder(borrower_key or folder_abs.name)
    if key != folder_abs.name:
        raise ValueError("borrower key must match the config folder")
    root = root or str(folder_abs.parent.parent)
    root_r = resolve_path(root)
    fleet_path = os.path.join(root_r, FLEET_FILENAME)
    fleet = None
    if os.path.exists(fleet_path):
        try:
            with open(fleet_path, encoding="utf-8") as fh:
                fleet = json.load(fh)
        except Exception:
            fleet = None
    if not isinstance(fleet, dict) or "borrowers" not in fleet:
        fleet = bootstrap(root)
    entry = _entry_from_folder(root_r, key)
    entry["config_file"] = f"{BORROWERS_DIR}/{key}/.record/current/config/borrower-config.json"
    for a in (aliases or []):
        if a and _norm(a) not in {_norm(x) for x in entry["aliases"]}:
            entry["aliases"].append(a)
    fleet["borrowers"] = [e for e in fleet.get("borrowers", [])
                          if _norm(e.get("borrower_key") or e.get("folder")) != _norm(entry["borrower_key"])]
    fleet["borrowers"].append(entry)
    fleet["borrowers"].sort(key=lambda e: _norm(e.get("borrower_key") or e.get("name")))
    save_fleet(fleet_path, fleet)
    return {"registry": fleet_path, "registered": entry}


def _attach_config_path(entry, fleet_path):
    cfg = os.path.join(os.path.dirname(fleet_path), entry.get("config_file", ""))
    cfg_r = resolve_path(cfg)
    entry["config_path"] = cfg_r
    entry["config_exists"] = os.path.exists(cfg_r)
    entry["setup_complete"] = False
    try:
        with open(cfg_r, encoding="utf-8") as fh:
            paths = (json.load(fh).get("paths") or {})
        model = paths.get("model_output_path")
        output = paths.get("monitor_output_folder")
        entry["setup_complete"] = bool(
            model and output and os.path.isfile(resolve_path(model))
            and os.path.isdir(resolve_path(output)))
        current = Path(cfg_r).resolve().parent.parent
        entry["model_migration_required"] = bool(
            model and current in Path(resolve_path(model)).resolve().parents)
    except (OSError, ValueError, AttributeError):
        pass
    return entry


def _monitor_output_folder(entry):
    try:
        with open(entry["config_path"], encoding="utf-8") as fh:
            return str((json.load(fh).get("paths") or {}).get("monitor_output_folder") or "")
    except (KeyError, OSError, ValueError, AttributeError):
        return ""


def _selection(entry, fleet_path):
    key = str(entry.get("borrower_key") or entry.get("folder") or "")
    try:
        borrower = Path(entry["config_path"]).resolve().parents[3]
        months = run_lib.read_published(borrower).get("months") or {}
    except (KeyError, IndexError, OSError, ValueError) as exc:
        return None, f"published selection is unreadable: {exc}"
    if not isinstance(months, dict) or not months:
        return None, None
    period = sorted(months)[-1]
    entry_at_period = months[period]
    run_id = str((entry_at_period or {}).get("run")
                if isinstance(entry_at_period, dict) else entry_at_period)
    try:
        event = run_lib._latest_selection_event(borrower, key, period)
    except (OSError, ValueError) as exc:
        return None, f"selection event is unreadable: {exc}"
    if not event or str(event.get("run_id")) != run_id:
        return None, "published selection has no matching selection event."
    return {"period": period, "run_id": run_id}, None


def _fact(found):
    return {key: found[key] for key in (
        "period", "run_id", "folder", "workbook_path", "memo_path", "roles"
    ) if key in found}


def _lifecycle(entry, fleet_path):
    monitor_output = _monitor_output_folder(entry)
    borrower = Path(entry["config_path"]).resolve().parents[3]
    selected_id, selection_warning = _selection(entry, fleet_path)
    warnings = [selection_warning] if selection_warning else []
    selected = None
    if selected_id:
        try:
            selected = _fact(run_lib.published_run(
                monitor_output, selected_id["period"], selected_id["run_id"]))
        except (OSError, ValueError) as exc:
            selected = dict(selected_id, folder=str(
                borrower / _RECORD_DIR / _RUNS_DIR
                / selected_id["period"] / selected_id["run_id"]))
            warnings.append(f"selected result cannot be opened automatically: {exc}")

    interrupted = []
    completed = []
    runs = borrower / _RECORD_DIR / _RUNS_DIR
    if runs.is_dir():
        for period_dir in sorted(runs.iterdir()):
            if not period_dir.is_dir() or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period_dir.name):
                warnings.append(f"invalid run period folder: {period_dir.name}")
                continue
            for run in sorted(period_dir.iterdir()):
                if not run.is_dir():
                    warnings.append(f"unexpected lifecycle item: {run.name}")
                    continue
                ledger = run_lib.read_ledger(run)
                manifest_exists = (run / run_lib.MANIFEST_NAME).exists()
                manifest = run_lib.read_manifest(run)
                if manifest is None:
                    if manifest_exists:
                        warnings.append(f"unreadable manifest for {period_dir.name} {run.name}")
                    if ledger:
                        if (str(ledger.get("period")) != period_dir.name
                                or str(ledger.get("run_id")) != run.name):
                            warnings.append(f"ledger identity mismatch for {period_dir.name} {run.name}")
                        else:
                            interrupted.append({
                                "period": period_dir.name, "run_id": run.name,
                                "folder": str(run),
                                "pending_steps": run_lib.pending_steps(ledger),
                            })
                    continue
                checked = run_lib.verify_manifest(run)
                if checked["status"] != "ok":
                    warnings.append(
                        f"run {period_dir.name} {run.name} does not verify ({checked['status']})")
                    continue
                try:
                    found = _fact(run_lib.verified_run(
                        monitor_output, period_dir.name, run.name))
                except ValueError as exc:
                    found = {"period": period_dir.name, "run_id": run.name,
                             "folder": str(run)}
                    warnings.append(
                        f"run {period_dir.name} {run.name} outputs are unavailable: {exc}")
                completed.append(found)

    try:
        discarded = {(e.get("period"), e.get("run_id"))
                     for e in run_lib._read_jsonl(run_lib._changelog_path(borrower))
                     if e.get("event") == "discarded"}
    except (OSError, ValueError) as exc:
        warnings.append(f"decision history is unreadable: {exc}")
        completed = []  # Do not offer selection when discard history is unknown.
        discarded = set()
    completed = [item for item in completed
                 if (item["period"], item["run_id"]) not in discarded]
    current = run_lib.read_manifest(run_lib.record_dir(borrower))
    if current and current.get("status") == "no_accepted_result":
        # Only a newly frozen run based on this state can need delivery now.
        try:
            state = run_lib.transaction.snapshot(borrower)
            completed = [item for item in completed
                         if (run_lib.read_ledger(item["folder"]) or {}).get("delivery_base") == state]
        except (OSError, ValueError) as exc:
            warnings.append(f"working state is unreadable: {exc}")
            completed = []
    completed.sort(key=lambda item: (item["period"], item["run_id"]))
    unpublished = completed[-1] if completed and (
        not selected_id
        or (completed[-1]["period"], completed[-1]["run_id"])
        != (selected_id["period"], selected_id["run_id"])) else None
    interrupted_run = max(interrupted, key=lambda item: (
        item["period"], item["run_id"]), default=None)
    finalization = []
    if completed:
        latest = completed[-1]
        if (not current or str(current.get("period")) != latest["period"]
                or str(current.get("run_id")) != latest["run_id"]):
            finalization.append({
                "period": latest["period"], "run_id": latest["run_id"],
                "warning": "finished run needs borrower-root/current finalization",
            })
    return {
        "selected_run": selected,
        "unpublished_run": unpublished,
        "interrupted_run": interrupted_run,
        "lifecycle_warnings": warnings,
        "finalization_warnings": finalization,
    }


def _with_journey(entry, fleet_path, inspect_lifecycle=False):
    entry = _attach_config_path(dict(entry), fleet_path)
    if not entry["setup_complete"]:
        if inspect_lifecycle:
            entry.update(_lifecycle(entry, fleet_path))
        entry["journey_state"] = "setup_incomplete"
        return entry
    selected_id, _warning = _selection(entry, fleet_path)
    if not inspect_lifecycle:
        entry["journey_state"] = "ready" if selected_id else "first_review_pending"
        if selected_id:
            entry["latest_completed_period"] = selected_id["period"]
        return entry
    facts = _lifecycle(entry, fleet_path)
    entry.update(facts)
    if facts["interrupted_run"]:
        entry["journey_state"] = "run_incomplete"
        entry.update({key: facts["interrupted_run"][key]
                      for key in ("run_id", "period", "pending_steps")})
    elif facts["selected_run"]:
        entry["journey_state"] = "ready"
        entry["latest_completed_period"] = facts["selected_run"]["period"]
    else:
        entry["journey_state"] = "first_review_pending"
    return entry


def context(query=None, fleet_path=None):
    """Return read-only borrower journey state without changing legacy resolve output."""
    fleet, path = load_fleet(fleet_path)
    configured_root = os.environ.get("CREDIT_MONITOR_STORE_ROOT")
    reachable_root = None
    if path:
        reachable_root = os.path.dirname(path)
    elif configured_root:
        candidate = resolve_path(configured_root)
        if os.path.isdir(candidate):
            reachable_root = candidate
    if fleet is None:
        return {
            "status": "no_registry",
            "journey_state": "new" if reachable_root else "unknown",
            "matches": [],
        }
    if not query:
        matches = [_with_journey(entry, path) for entry in fleet.get("borrowers", [])]
        matches.sort(key=lambda entry: (
            entry.get("journey_state") not in ("setup_incomplete", "run_incomplete"),
            _norm(entry.get("name") or entry.get("borrower_key")),
        ))
        return {"status": "list", "matches": matches, "registry": path}
    out = resolve(query, fleet)
    out["registry"] = path
    if out["status"] == "found":
        match = _with_journey(out["matches"][0], path, inspect_lifecycle=True)
        out["matches"] = [match]
        out["journey_state"] = match["journey_state"]
        for key in ("run_id", "period", "pending_steps", "latest_completed_period",
                    "selected_run", "unpublished_run", "interrupted_run",
                    "lifecycle_warnings", "finalization_warnings",
                    "model_migration_required"):
            if key in match:
                out[key] = match[key]
    elif out["status"] == "not_found":
        out["journey_state"] = "new"
    return out


def _cli(argv=None):
    ap = argparse.ArgumentParser(description="credit monitoring borrower registry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("find")
    r = sub.add_parser("resolve")
    r.add_argument("query")
    r.add_argument("--fleet")
    c = sub.add_parser("context")
    c.add_argument("query", nargs="?")
    c.add_argument("--fleet", required=True)
    c.add_argument("--source-root", required=True)
    c.add_argument("--store-root", required=True)
    b = sub.add_parser("bootstrap")
    b.add_argument("--root", required=True)
    b.add_argument("--write", action="store_true")
    g = sub.add_parser("register")
    g.add_argument("--config", required=True)
    g.add_argument("--root")
    g.add_argument("--alias", action="append")
    g.add_argument("--borrower-key")
    a = ap.parse_args(argv)

    if a.cmd == "find":
        print(json.dumps({"registry": find_fleet_path()}))
    elif a.cmd == "resolve":
        fleet, path = load_fleet(a.fleet)
        if fleet is None:
            print(json.dumps({"status": "no_registry", "matches": []}))
            return
        out = resolve(a.query, fleet)
        out["registry"] = path
        if out["status"] == "found":
            _attach_config_path(out["matches"][0], path)
        print(json.dumps(out, ensure_ascii=False))
    elif a.cmd == "context":
        if not os.path.isabs(a.fleet):
            ap.error("--fleet must be an absolute path")
        expected_fleet = os.path.join(os.path.abspath(a.store_root), FLEET_FILENAME)
        if os.path.realpath(a.fleet) != os.path.realpath(expected_fleet):
            ap.error("--fleet must be the registry inside --store-root")
        os.environ[run_lib.SOURCE_ROOT_ENV] = os.path.abspath(a.source_root)
        os.environ[run_lib.STORE_ROOT_ENV] = os.path.abspath(a.store_root)
        print(json.dumps(context(a.query, a.fleet), ensure_ascii=False))
    elif a.cmd == "bootstrap":
        fleet = bootstrap(a.root)
        if a.write:
            fp = os.path.join(resolve_path(a.root), FLEET_FILENAME)
            save_fleet(fp, fleet)
            fleet["_written_to"] = fp
        print(json.dumps(fleet, ensure_ascii=False))
    elif a.cmd == "register":
        print(json.dumps(register(a.config, a.root, a.alias, a.borrower_key), ensure_ascii=False))


if __name__ == "__main__":
    _cli()
