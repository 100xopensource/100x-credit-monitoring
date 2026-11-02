"""Presentation of analyst-owned views; explicit unsigned notes withhold judgments."""

JUDGMENT_FIELDS = ("watchlist", "trajectory", "thesis_status")
RETIRED_JUDGMENT_FIELDS = ("risk_rating",)


def settled(value):
    if not isinstance(value, str):
        return value
    flags = ("placeholder", "tbd", "tbu", "to be updated", "fill in", "xxx",
             "no analyst", "not a credit opinion", "must be replaced", "auto-set",
             "provisional", "awaiting analyst sign-off", "awaiting sign-off",
             "not analyst-signed", "not signed off", "claude-proposed", "agent-proposed",
             "auto-drafted", "assistant-drafted")
    return None if any(flag in value.lower() for flag in flags) else value


def settled_fields(record, fields):
    record = record or {}
    retired = set(RETIRED_JUDGMENT_FIELDS)
    retired.update(f"_{f}_note" for f in RETIRED_JUDGMENT_FIELDS)
    retired.update(f"{f}_provenance" for f in RETIRED_JUDGMENT_FIELDS)
    notes = {k: v for k, v in record.items() if k not in retired and
             (str(k).startswith("_") or k == "provenance" or k.endswith("_provenance"))}
    field_notes = {key for f in fields for key in (f"_{f}_note", f"{f}_provenance")}
    def unsigned(note):
        return isinstance(note, str) and (settled(note) is None or note.strip().lower() == "proposed")

    whole_block = any(unsigned(v) for k, v in notes.items() if k not in field_notes)
    out, disclaimed = {}, []
    for field in fields:
        withheld = whole_block or any(
            unsigned(notes.get(key))
            for key in (f"_{field}_note", f"{field}_provenance"))
        out[field] = None if withheld else settled(record.get(field))
        if out[field] is None and record.get(field) not in (None, ""):
            disclaimed.append(field)
    return out, disclaimed


def settled_view(record):
    return settled_fields(record, JUDGMENT_FIELDS)


def settled_thesis(pillars):
    """Keep evidence and pillar text, but not an explicitly unsigned grade."""
    return [dict(pillar, **settled_fields(pillar, ("status",))[0]) for pillar in pillars]
