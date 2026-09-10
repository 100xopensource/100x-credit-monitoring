#!/usr/bin/env python3
"""rows_lib.py — the four row shapes every monitoring answer is made of.

Sorting every question the analyst, the portfolio manager and the partner ask by
what an answer NEEDS leaves four shapes:

    test          a declared level, the value tested against it, the room between
    series        one named quantity, one deal, across periods
    composition   one number split into parts that sum to it
    judgment      a call someone made, with a reason (coverage included)

Everything else is a filter, a rank or an aggregate over those. So the database
is four tables rather than one per module, and a question nobody wrote down in
advance is assembled from the same rows instead of from new code.

This module defines what a row IS: the spine every shape carries, the fields each
shape adds, how a figure's address is made portable, and how the declared layer
(the deal's covenant spec) is read. `build_rows.py` uses it to translate archived
module output; nothing here reads a store or writes a file.

Two rules hold the shapes together:

* **Every field is emitted on every row, with a concrete type.** A key omitted
  when its value is null makes one deal's file infer one type for a column and
  another deal's file infer a different one, and the JSON accessor then fails on
  exactly the rows that carry data. Querying files in place makes "omit the null
  key" a correctness problem, not a style choice.
* **Only the view that owns a property sets it.** The same figure reaches the
  rows through more than one view, so a field set on all of them is counted more
  than once.

Stdlib only, and deliberately independent of `monitor_lib` (which imports
openpyxl at module level for the workbook reader) so a caller that only wants
rows does not need a spreadsheet library.
"""
import json
import os
import re
from urllib.parse import unquote, urlsplit

SHAPES = ("test", "series", "composition", "judgment")

_HERE = os.path.dirname(os.path.abspath(__file__))
_CATALOG_PATH = os.path.join(_HERE, "..", "config", "store-catalog.json")
_METRIC_CORE_PATH = os.path.join(_HERE, "..", "config", "metric-core.json")

# ---------------------------------------------------------------------------
# Controlled columns
#
# A controlled column holds ONE spelling per thing, so a cross-deal query can
# group on it. An unmapped value leaves the controlled column null and keeps the
# deal's own words in the `_verbatim` column beside it — passing a module's own
# section name straight through gave `statement` five spellings for three
# statements.
# ---------------------------------------------------------------------------
STATEMENTS = {"income": "is", "income_statement": "is", "is": "is",
              "balance": "bs", "balance_sheet": "bs", "bs": "bs",
              "cash_flow": "cf", "cashflow": "cf", "cf": "cf", "kpi": "kpi"}

BASES = {"actual": "actual", "actuals": "actual", "budget": "budget",
         "plan": "budget", "forecast": "forecast", "reforecast": "forecast",
         "restated": "restated", "certified": "certified"}

PERIOD_TYPES = ("month", "ytd", "ttm", "t3m", "pro_forma", "fiscal_period", "custom")

# An author's own disclosure that a figure is soft. The design makes this a
# declared `fidelity` field; today it exists only as prose in the covenant spec,
# which is why it is recovered by reading rather than by being told.
_SOFT = [
    (r"do not score|not to be scored|must not be scored", "do_not_score"),
    (r"not a reliable automated figure|unreliable", "do_not_score"),
    (r"approximat", "approximate"),
    (r"known understatement|known overstatement|reads high|reads low", "approximate"),
    (r"stated floor only|stated only|floor only", "stated_only"),
    (r"no multiplier|no scale-factor|scale.factor support", "approximate"),
    (r"placeholder|proxy for", "approximate"),
]
FIDELITY_RANK = ("computed", "stated_only", "approximate", "do_not_score")

_MONTH_NAME = re.compile(r"^([A-Z][a-z]{2})[a-z]* (\d{4})$")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
_ISO_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


# ---------------------------------------------------------------------------
# Small readers of declared data
# ---------------------------------------------------------------------------
def load_metric_core(path=None):
    """The controlled core: {label as written: metric}. The few dozen lines every
    reader watches, which every deal's own labels map into.

    It ships as data (`config/metric-core.json`) because which lines matter is a
    credit call that changes without a release. An absent or malformed file
    leaves every metric unmapped rather than raising: a row keeps the deal's own
    label either way, so the cross-deal question degrades and the per-deal one
    still works.
    """
    try:
        with open(path or _METRIC_CORE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    labels = data.get("labels") if isinstance(data, dict) else None
    if not isinstance(labels, dict):
        return {}
    return {str(k).strip().lower(): str(v) for k, v in labels.items() if k and v}


def load_store_leaves(path=None):
    """{store_id: [folder names that identify it]} from the baked store catalog.

    This is what makes an address portable without a hardcoded folder name: the
    catalog already knows that `the long monitoring store name` and
    `store` are the same shared folder, so a recorded path is placed by finding
    one of those names in it.
    """
    try:
        with open(path or _CATALOG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for entry in (data.get("stores") if isinstance(data, dict) else data) or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        leaves = set()
        # The shortcut name an analyst ends up with, and the folder's own name
        # in SharePoint (the tail of url_base) — which is what appears in the
        # path when the shortcut was added at an ENCLOSING level.
        parts = [p for p in re.split(r"[\\/]+", str(entry.get("local_leaf") or "")) if p]
        if parts:
            leaves.add(parts[-1])
        tail = [unquote(seg) for seg in
                urlsplit(str(entry.get("url_base") or "")).path.split("/") if seg]
        if tail:
            leaves.add(tail[-1])
        # The folder's own short name is a leaf too: an analyst who connected the
        # folder directly has no "<Site> - <folder>" prefix in their path.
        for leaf in list(leaves):
            tail = leaf.rsplit(" - ", 1)[-1].strip()
            if tail:
                leaves.add(tail)
        if leaves:
            out[str(entry["id"])] = sorted(leaves, key=len, reverse=True)
    return out


def read_covenant_spec(path):
    """The declared layer, as far as it exists today: one deal's covenant spec.

    Three things live here and nowhere else, which is why a row that wants them
    has to read the spec rather than the module output:

    * the **direction** of each test (`primitive`) — a certificate states a level,
      never whether passing is above or below it, so scoring a certified figure
      with an assumed `>=` inverts every max-style covenant;
    * the author's own **reliability disclosures**, written as prose in
      `definitions.<name>.note` and `covenants[i]._note` / `_threshold_note`,
      which reach no module output;
    * whether the deal declares an **either/or group** at all.

    Returns {} for an unreadable spec: a deal with no declared layer still
    produces rows, with `declared_by` null saying so.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            spec = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(spec, dict):
        return {}

    stamp = os.path.basename(path)
    definitions = {}
    for name, block in (spec.get("definitions") or {}).items():
        note = block.get("note") if isinstance(block, dict) else None
        definitions[name] = {"fidelity": fidelity_of(note), "note": note}

    covenants = {}
    for cov in spec.get("covenants") or []:
        if not isinstance(cov, dict) or not cov.get("id"):
            continue
        notes = [cov.get(k) for k in ("_note", "_threshold_note", "note", "units_note")]
        refs = [(cov.get(k) or {}).get("ref") for k in ("metric", "numerator", "denominator")
                if isinstance(cov.get(k), dict)]
        refs = [r for r in refs if r]
        worst = fidelity_of(*notes)
        for ref in refs:
            worst = worse_fidelity(worst, definitions.get(ref, {}).get("fidelity", "computed"))
        covenants[str(cov["id"])] = {
            "fidelity": worst,
            "primitive": cov.get("primitive"),
            "declared_by": stamp,
            "note": next((n for n in notes if n), None),
            "definition_refs": refs,
        }

    return {"definitions": definitions, "covenants": covenants,
            "has_groups": bool(spec.get("either_or_groups")),
            "declared_by": stamp}


# ---------------------------------------------------------------------------
# Field normalizers
# ---------------------------------------------------------------------------
def core_metric(label, core):
    """The controlled metric for a label, or None when nothing maps it."""
    if label is None:
        return None
    return core.get(str(label).strip().lower())


def statement_of(section):
    return STATEMENTS.get(str(section or "").strip().lower())


def basis_of(basis):
    return BASES.get(str(basis or "").strip().lower())


def iso_month(text):
    """'Jun 2026' / '2026-06' / '2026-06-30' -> '2026-06'; anything else None.

    None rather than a raise: a period that is not a month is already in the
    store (`certified-values-2026.json`), and the row takes its period from the
    figure in that case instead of the run failing.
    """
    if text is None:
        return None
    s = str(text).strip()
    m = _MONTH_NAME.match(s)
    if m and m.group(1) in _MONTHS:
        return f"{m.group(2)}-{_MONTHS[m.group(1)]:02d}"
    m = re.match(r"^(\d{4})-(\d{2})", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def is_iso_month(text):
    return bool(_ISO_MONTH.match(str(text or "")))


def num(v):
    """The value if it is a real number, else None. Booleans are not numbers."""
    if isinstance(v, bool):
        return None
    return v if isinstance(v, (int, float)) else None


def fidelity_of(*texts):
    """How much a figure can be trusted, read from its author's own words."""
    blob = " ".join(str(t) for t in texts if t).lower()
    for pattern, verdict in _SOFT:
        if re.search(pattern, blob):
            return verdict
    return "computed"


def worse_fidelity(a, b):
    """The less trustworthy of two verdicts — a covenant inherits the worst
    disclosure of its own note and every definition it is computed from."""
    rank = {v: i for i, v in enumerate(FIDELITY_RANK)}
    return a if rank.get(a, 0) >= rank.get(b, 0) else b


def window_type(window):
    """A covenant window as (label, period_type).

    `window` is 'spot' | 'ttm' | 't3m' OR {'custom_months': N}; custom windows
    map to the `custom` period type.
    """
    if isinstance(window, dict):
        n = window.get("custom_months")
        return (f"custom_{n}m" if n else "custom"), "custom"
    label = str(window) if window is not None else None
    return label, {"ttm": "ttm", "t3m": "t3m"}.get(label, "month")


def currency_of(unit):
    """The ledger's currency from a module's `unit` field, when it names one."""
    return {"usd": "USD", "cad": "CAD", "gbp": "GBP", "eur": "EUR"}.get(
        str(unit or "").strip().lower())


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------
# A configured path names its shared folder up front: "store:Example Borrower/_model/x.json".
# The leading name is a store id, and what follows the colon is never a separator
# (which is what keeps a Windows drive letter, "C:\\Users\\...", from matching).
_STORE_PREFIXED = re.compile(r"^([A-Za-z][A-Za-z0-9_]*):(?![\\/])(.*)$")


def store_path(path, store_leaves, monitoring_store=None, deal_folder=None):
    """(store_id, path_in_store) for a recorded path, or (None, None).

    A path a current engine recorded says its shared folder itself and is simply
    read. The folder-name matching below is for the outputs already sitting in
    the archive, which recorded where the run happened to read the file.

    Local paths vary by user and sync configuration. The portable pointer is
    the shared store plus the path below that store's root, which each reader
    re-roots at its own copy.

    The store is found by matching a catalog folder name as a whole path segment
    or the END of one after a separator, which is what makes both
    `.../3_Current Portfolio/Example Borrower` and `... - Credit - Other - 3_Current Portfolio`
    resolve to the same store. A leaf that merely trails inside a longer word is
    a different folder that happens to end in the same letters.

    `deal_folder` is the last resort for a sandbox mount path
    (`/sessions/<id>/mnt/...`), which names no shared folder at all: the borrower
    folder we are reading is in it, so the path below that folder is still a
    usable address inside the monitoring store.
    """
    if not path:
        return None, None
    prefixed = _STORE_PREFIXED.match(str(path).strip())
    if prefixed:
        return prefixed.group(1), prefixed.group(2).replace("\\", "/").strip("/")
    segments = [s for s in re.split(r"[\\/]+", str(path).strip()) if s]
    for store_id, leaves in (store_leaves or {}).items():
        for i, segment in enumerate(segments[:-1]):
            low = segment.lower()
            for leaf in leaves:
                leaf = leaf.lower()
                if low == leaf or (low.endswith(leaf)
                                   and not low[:-len(leaf)][-1:].isalnum()):
                    return store_id, "/".join(segments[i + 1:])
    if deal_folder:
        want = str(deal_folder).lower()
        for i, segment in enumerate(segments[:-1]):
            if segment.lower() == want:
                return (monitoring_store or "store"), "/".join(segments[i:])
    return None, None


# What every address carries, whether or not the figure's own ref named it. The
# spine rule applies one level down: a ref that omits `section` where another ref
# has it leaves the column out of the struct entirely, and a query that reads it
# cannot bind. A producer's extra locating detail (a page, a quoted clause) rides
# along on top of these.
REF_FIELDS = {
    "file": None,
    "store": None,
    "path_in_store": None,
    "source_path": None,
    "sheet": None,
    "row": None,
    "cells": None,
    "label": None,
    "section": None,
    "document": None,
    # where in a DOCUMENT a figure was read, as a workbook ref carries sheet+cells.
    # A transcribed certificate figure records this so it can be checked without
    # reopening the certificate.
    "page": None,
    "upstream": None,
    "address_from": None,
    "unresolved_reason": None,
}


def address(ref, source_files=None, store_leaves=None, deal_folder=None,
            monitoring_store="store"):
    """One `source_ref` made followable on its own.

    Two shapes reach us, and the row says which answered:

    * **producer** — an output written by a current engine already carries
      `store` + `path_in_store` (and sometimes `upstream`, the borrower's own
      document behind the figure). It is taken as given.
    * **derived** — an older output names its file as `{"file": 0}`, a position in
      that run's `source_files` list. The index means nothing away from the list
      and the list is not on the row, so the ref keeps the sheet and the cell and
      loses the file. It is resolved here, once, where every shape passes through.

    Both paths always set `store`, `path_in_store`, `address_from` and
    `unresolved_reason`, so a query can measure how far the book's addresses
    actually reach instead of assuming.
    """
    if not isinstance(ref, dict):
        return None
    out = dict(REF_FIELDS)
    out.update(ref)

    if ref.get("store") and ref.get("path_in_store"):
        out["store"] = ref["store"]
        out["path_in_store"] = ref["path_in_store"]
        out["address_from"] = "producer"
        out["unresolved_reason"] = None
        return out

    out["address_from"] = "derived"
    index = ref.get("file")
    files = source_files or []
    if isinstance(index, bool) or not isinstance(index, int):
        out["store"] = out["path_in_store"] = out["source_path"] = None
        out["unresolved_reason"] = ("the document is named, not located"
                                    if ref.get("document") else "the ref names no file")
        return out
    if not 0 <= index < len(files):
        out["store"] = out["path_in_store"] = out["source_path"] = None
        out["unresolved_reason"] = (f"file index {index} against {len(files)} "
                                   "source_files on the run")
        return out

    recorded = str(files[index])
    store, rel = store_path(recorded, store_leaves, monitoring_store, deal_folder)
    out["source_path"] = recorded
    out["store"], out["path_in_store"] = store, rel
    out["unresolved_reason"] = None if rel else (
        "recorded under no shared folder we know, so it only opens on the machine "
        "that produced it")
    return out


def addresses(refs, source_files=None, store_leaves=None, deal_folder=None,
              monitoring_store="store"):
    """`address()` over whatever a module put in `source_ref` — one ref, a list of
    them, or nothing. Always a list, so the column has one type."""
    if refs is None:
        return []
    if isinstance(refs, dict):
        refs = [refs]
    if not isinstance(refs, list):
        return []
    out = []
    for ref in refs:
        resolved = address(ref, source_files, store_leaves, deal_folder, monitoring_store)
        if resolved is not None:
            out.append(resolved)
    return out


# ---------------------------------------------------------------------------
# The rows
# ---------------------------------------------------------------------------
# The spine: what every row of every shape carries. Defaults are the
# concrete null of each field's type, and every one is written to every row.
SPINE = {
    "deal_id": None,
    "deal_name": None,
    "store_folder": None,
    "period": None,
    "period_type": "month",
    "run_id": None,
    "run_id_from": None,
    "produced_at": None,
    "produced_by": None,
    "run_stamp_from": None,
    "module": None,
    "engine_version": None,
    "archived": True,
    "value": None,
    # How many dollars this row's value stands for: 1 for a model in whole
    # dollars, 1000 for one in thousands. Declared per borrower and carried from
    # the output's envelope, so two borrowers can be ranked against each other.
    # Null on a figure that is not money, and on an output that predates the
    # declaration.
    "scale": None,
    "currency": None,
    "unit_verbatim": None,
    "source_ref": [],
    "fidelity": "computed",
    "fidelity_source": None,
    "fidelity_note": None,
    "declared_by": None,
    "exception_id": None,
}

SHAPE_FIELDS = {
    "test": {
        "test_id": None,
        "test_kind": None,          # covenant | budget_variance | certified_covenant
        "metric": None,
        "label_verbatim": None,
        "primitive": None,          # the DIRECTION of the test — declared, not guessed
        "window": None,
        "threshold_value": None,
        "threshold_kind": None,     # scalar | schedule | declared_budget | unresolved
        "threshold_source": None,
        "threshold_effective": None,
        "status": None,             # pass | breach | at_risk | not_evaluable
        "headroom_abs": None,
        "headroom_pct": None,       # a FRACTION of the threshold, not a percentage
        "headroom_unit": "fraction_of_threshold",
        "favorable": None,
        "group_id": None,
        "is_group_row": False,
        "group_rule": None,
        "group_status": None,
        "group_members": None,
        "group_leg_statuses": None,
        "held_by_at_risk_leg": None,
        "cert_value": None,
        "cert_status": None,
        "cert_agrees": None,
        "delta_model_vs_cert": None,
        "agreement_ref": None,
        "unscored_reason": None,
    },
    "series": {
        "metric": None,
        "label_verbatim": None,
        "statement": None,          # is | bs | cf | kpi
        "section_verbatim": None,
        "basis": None,              # actual | budget | forecast | restated | certified
        "basis_verbatim": None,
        "volume": None,
        "volume_unit": None,
    },
    "composition": {
        "parent_metric": None,
        "section_verbatim": None,
        "source_view": None,        # which view produced it
        "part_label": None,
        "part_verbatim": None,
        "share_of_parent": None,
        "cost_behaviour": None,     # fixed | variable | semi_variable | unclassified
        "behaviour_declared_by": None,
        "flags": None,
    },
    "judgment": {
        "kind": None,               # rating | watchlist | reliability | exception |
                                    # divergence | coverage
        "subject": None,
        "call": None,
        "reason": None,
        "severity": None,
        "decided_by": None,         # engine | agent | analyst | translator
        "supersedes": None,
    },
}

# What the RUN says about every row it produces, as opposed to what one figure
# says about itself. The unit is here because it is declared once per borrower and
# holds for every figure in the output; a figure that is not money — a
# ratio, a number of days — overrides scale and currency to null on its own row.
_CTX_FIELDS = ("deal_id", "deal_name", "store_folder", "period", "run_id",
               "run_id_from", "produced_at", "produced_by", "run_stamp_from",
               "module", "engine_version", "archived",
               "scale", "currency", "unit_verbatim")

# What a row that is NOT money says about its unit: nothing. Spread this into a
# row builder (`**NOT_MONEY`) and the borrower's money scale stops travelling with
# a figure it does not describe.
NOT_MONEY = {"scale": None, "currency": None, "unit_verbatim": None}

# The covenant primitives whose figure is a RATIO, so neither the actual nor the
# threshold is money: 3.13x is 3.13x on any scale, and both sides come off the same
# model. Two places need this same list -- resolving a threshold, where a spec-level
# "dollars" must not reach a leverage covenant, and writing that covenant's row. It
# lives here because this module is the one both of them already import.
RATIO_PRIMITIVES = ("max_ratio", "min_coverage")


def row(shape, ctx, **fields):
    """One row of `shape`: the spine, the shape's own fields, then what the reader
    passes — every key present, with its own concrete null when unset.

    An unknown field raises. A reader that invents a column by typo would
    otherwise write it to one deal's file and not another's, which is the exact
    shape of the type-inference failure the fixed field set exists to prevent.
    """
    if shape not in SHAPE_FIELDS:
        raise ValueError(f"unknown shape: {shape!r}")
    out = dict(SPINE)
    out.update(SHAPE_FIELDS[shape])
    for key in _CTX_FIELDS:
        if key in ctx:
            out[key] = ctx[key]
    unknown = [k for k in fields if k not in out]
    if unknown:
        raise ValueError(f"{shape} row has no field(s): {', '.join(sorted(unknown))}")
    out.update(fields)
    if out["period"] is None:
        out["period"] = ctx.get("period")
    if out["period_type"] not in PERIOD_TYPES:
        raise ValueError(f"unknown period_type: {out['period_type']!r}")
    out["source_ref"] = addresses(
        out["source_ref"], ctx.get("source_files"), ctx.get("store_leaves"),
        ctx.get("store_folder"), ctx.get("monitoring_store", "store"))
    return out


def apply_declared(row_out, declared, test_id):
    """Put the deal's declared verdict on a test row: who declared it, which
    definitions it rests on, and the author's own reliability disclosure where
    there is one. The output's prose says nothing about reliability; the spec
    does, so the spec is what a query reads."""
    entry = ((declared or {}).get("covenants") or {}).get(str(test_id))
    if not entry:
        return row_out
    row_out["declared_by"] = entry.get("declared_by")
    if entry.get("primitive") and not row_out.get("primitive"):
        row_out["primitive"] = entry["primitive"]
    if entry.get("fidelity", "computed") != "computed":
        row_out["fidelity"] = entry["fidelity"]
        row_out["fidelity_source"] = "covenant-spec prose (not in the module output)"
        row_out["fidelity_note"] = (entry.get("note") or "")[:400] or None
    return row_out


class Rows:
    """The four lists a translation run fills, plus the notes it wants to say out
    loud. `notes` are about the RUN (an unparsed filename, a module with no
    reader); anything about a DEAL's month belongs in a judgment row, where it
    can be queried."""

    def __init__(self):
        self.test = []
        self.series = []
        self.composition = []
        self.judgment = []
        self.notes = []

    def add(self, shape, ctx, **fields):
        getattr(self, shape).append(row(shape, ctx, **fields))

    def counts(self):
        return {shape: len(getattr(self, shape)) for shape in SHAPES}
