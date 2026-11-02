"""Present the engine's unsettled covenant tests without changing its figures."""


def presented_results(payload):
    results = payload.get("results") or []
    if not payload.get("reconciliation_required") or payload.get("reconciliation_override"):
        return results
    blocked = {(b.get("covenant"), b.get("test_date")): b.get("trigger")
               for b in payload.get("reconciliation_blockers") or []}
    out = []
    for row in results:
        key = (row.get("id"), row.get("test_date"))
        group_blocked = any((member, row.get("test_date")) in blocked
                            for member in row.get("members") or [])
        if key in blocked or group_blocked:
            row = dict(row, status="unresolved", reconciliation_reason=(
                "Certified and model figures require reconciliation; no verdict is settled. "
                + (blocked.get(key) or "An either/or member requires reconciliation.")))
        out.append(row)
    unresolved_groups = {(r.get("id"), r.get("test_date")) for r in out
                         if r.get("members") and r.get("status") == "unresolved"}
    return [dict(r, group_status="unresolved")
            if (r.get("either_or_group"), r.get("test_date")) in unresolved_groups else r
            for r in out]
