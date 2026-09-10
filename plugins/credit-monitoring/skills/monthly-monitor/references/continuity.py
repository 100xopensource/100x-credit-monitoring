"""continuity.py — deterministic memory plumbing for monthly-monitor.

This module does ONLY the file plumbing that makes the monitor remember across months:
load a verified prior run's memo/state, load the deal-context, and write state back.

The JUDGMENT (which prior items are actually resolved, what this month's new flags are) is
the orchestrator/agent's job — it passes the results in. Nothing here touches a computed
financial figure; it only maintains carry_forward + last_reviewed in deal-context.json.

Borrower-agnostic: no deal-specific assumptions. Periods are 'YYYY-MM'.
"""

import json
import os
import datetime

try:
    from plugin.lib import run_lib
except ModuleNotFoundError:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "..", "lib"))
    import run_lib


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"run state is not a JSON object: {path}")
    return data


def load_prior_state(base_folder, manifest, current_context_path=None):
    """Load continuity only from one explicitly supplied, verified base run."""
    base_folder = os.path.abspath(os.fspath(base_folder))
    stored = run_lib.read_manifest(base_folder)
    if stored != manifest or run_lib.verify_manifest(base_folder)["status"] != "ok":
        raise ValueError("the supplied continuity base does not match its verified manifest.")

    files = manifest.get("files") or {}
    try:
        roles = run_lib.validate_manifest_roles(base_folder, manifest, required=())
    except ValueError as exc:
        roles = {}
        memo_fallback = f"prior memo unavailable: {exc}"
    else:
        memo_fallback = None
    memo_rel = roles.get("memo")
    memo_path = (os.path.join(base_folder, *memo_rel.split("/"))
                 if memo_rel else None)
    if not memo_path and memo_fallback is None:
        memo_fallback = "prior memo unavailable: the verified manifest has no memo role."

    carry_forward = {}
    carry_rel = "carry-forward.json"
    if carry_rel in files:
        carry_forward = _read_json(os.path.join(base_folder, carry_rel))

    context_rel = "config/deal-context.json"
    context = None
    context_source = None
    fallback = memo_fallback
    if context_rel in files:
        context = _read_json(os.path.join(base_folder, *context_rel.split("/")))
        context_source = "base-run"
    elif current_context_path and os.path.isfile(current_context_path):
        context = _read_json(current_context_path)
        context_source = "current-fallback"
        note = "older verified base has no archived context; using visible current context fallback."
        fallback = f"{fallback} {note}".strip() if fallback else note

    ledger = (_read_json(os.path.join(base_folder, run_lib.LEDGER_NAME))
              if run_lib.LEDGER_NAME in files else {})
    return {
        "memo_path": memo_path,
        "memo_status": "verified-role" if memo_path else "unavailable",
        "carry_forward": carry_forward,
        "context": context,
        "context_source": context_source,
        "analyst_focus": ledger.get("analyst_focus") or [],
        "fallback": fallback,
    }


def load_deal_context(deal_context_path):
    with open(deal_context_path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_back(deal_context_path, *, open_requests, unresolved_flags,
               last_period_headline, analyst_focus=None, reviewed_by="monitor",
               trigger="monthly", carry_forward_path=None):
    """Re-seed carry_forward from this cycle's results and stamp last_reviewed. Returns the path.

    analyst_focus: this run's focus items as [{item, asked_period, finding}] — what the analyst
    asked this run to look at and the memo's one-line answer, read back to them at the start of
    the next run. None leaves the stored list untouched.

    carry_forward_path: optional standalone run output for the next verified run to load.

    Only carry_forward and last_reviewed are modified; static / our_view are left untouched
    (our_view is analyst-owned). The objective model + covenant-spec are never written here.
    """
    dc = load_deal_context(deal_context_path)
    cf = dc.setdefault("carry_forward", {})
    cf["open_requests_to_borrower"] = open_requests
    cf["unresolved_flags"] = unresolved_flags
    cf["last_period_headline"] = last_period_headline
    if analyst_focus is not None:
        cf["analyst_focus"] = analyst_focus
    dc["last_reviewed"] = {
        "date": datetime.date.today().isoformat(),
        "trigger": trigger,
    }
    if carry_forward_path is not None:
        written = run_lib.write_output(cf, carry_forward_path)
        if _read_json(written) != cf:
            raise OSError(f"carry-forward read-back failed: {written}")
    run_lib.write_output(dc, deal_context_path)
    return deal_context_path
