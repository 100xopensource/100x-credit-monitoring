#!/usr/bin/env python3
"""Write the flat pages read by the Credit Monitoring portfolio artifact.

Every page is derived from a verified, manifest-checked run selected in `published.json`.
The builder never chooses a run by directory order and never reads an unpublished
run. It writes local files only; optional folder synchronization is outside it.

Usage:
    python build_artifact.py --store <credit-monitoring-output> --out <Artifact>
    python build_artifact.py --store <path> --out <path> --only <borrower-key>
    python build_artifact.py --store <path> --out <path> --stale
    python build_artifact.py --out <path> --check

Dependencies: Python stdlib, plus openpyxl for workbook pages. Without openpyxl,
the borrower still builds and the workbook page reports why it is unavailable.
"""
import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "lib"))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

try:
    import monitor_lib as ml
    import run_lib
except Exception:                                            # pragma: no cover
    ml = None
    import run_lib

CONTRACT_VERSION = 1
INDEX_SCHEMA = "credit-monitor-artifact-index/v1"
DEAL_SCHEMA = "credit-monitor-artifact-deal/v1"
WORKBOOK_SCHEMA = "credit-monitor-artifact-workbook/v1"
NARRATIVE_SCHEMA = "credit-monitor-artifact-narrative/v1"

# The modules the memo and the grid read. A module absent from a borrower's
# archive is reported as missing rather than assumed empty.
MODULES = (
    "covenant-compliance",
    "three-statement-analysis",
    "budget-vs-actual",
    "ap-ar-aging",
    "revenue-quality",
)

# Twenty-four months is the most that stays legible at grid-cell size.
SPARK_MONTHS = 24

_ARCHIVE_NAME = re.compile(r"^(?P<module>.+?)-(?P<period>\d{4}-\d{2})(?P<skipped>\.skipped)?\.json$")
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def slugify(text):
    return _SLUG_STRIP.sub("-", str(text or "").lower()).strip("-") or "borrower"


def read_json(path):
    """Parse a JSON file, or None when it is absent or malformed. A malformed
    input is a missing input: the borrower is reported unreadable, not crashed."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def resolve(path):
    """A configured path as this session can open it (sandbox mounts included)."""
    if ml is not None:
        try:
            return ml.resolve_path(path)
        except Exception:
            pass
    return path


DASHBOARD_FOLDER_NAME = "Artifact"
# The catalog id of the dashboard folder. A first build asks the catalog where
# that folder is, so a folder the catalog does not name cannot receive one.

# What the dashboard folder holds. A refresh writes the first three; `fonts.html`
# and `.dashboard` are put there once and left alone (Rule 2 of the skill).
# `desktop.ini` and `Thumbs.db` are Windows' own, and a fresh OneDrive folder can
# hold one before anything is published into it.
_PAGE_SUFFIXES = ("--deal.json", "--memo.html", "--workbook.json")
_KEPT_NAMES = ("index.json", "fonts.html", ".dashboard", "desktop.ini", "thumbs.db")


def foreign_names(out_dir):
    """Names in `out_dir` that no dashboard folder should hold."""
    try:
        names = os.listdir(out_dir)
    except OSError:
        return []
    odd = []
    for name in names:
        if name.lower() in _KEPT_NAMES or name.startswith("."):
            continue
        if name.startswith("narrative--") and name.endswith(".json"):
            continue
        if any(name.endswith(suffix) for suffix in _PAGE_SUFFIXES):
            continue
        odd.append(name)
    return sorted(odd)


def dashboard_index(out_dir):
    """`out_dir`'s `index.json` when it IS a dashboard index, else None.

    The file being there is not the proof. Any folder that keeps an `index.json`
    of its own then reads as a published dashboard, and the build writes 60
    pages into it.
    """
    index = read_json(os.path.join(out_dir, "index.json"))
    if not isinstance(index, dict):
        return None
    if index.get("schema") != INDEX_SCHEMA:
        return None
    if not isinstance(index.get("borrowers"), list):
        return None
    return index


def refuse_wrong_folder(out_dir):
    """Stop before writing when `out_dir` is not the dashboard folder."""
    inner = os.path.join(out_dir, DASHBOARD_FOLDER_NAME)
    if os.path.isdir(inner):
        raise SystemExit(
            "The dashboard folder is one level down, at:\n  %s\nYou named the folder above it. "
            "Nothing was written. Pass that path instead." % inner)

    if os.path.isfile(os.path.join(out_dir, "index.json")):
        if dashboard_index(out_dir) is not None:
            return                    # a published book. Its own index is the proof.
        raise SystemExit(
            "The index.json in this folder is not a dashboard index (%s):\n  %s\n"
            "Nothing was written. Name the dashboard folder itself."
            % (INDEX_SCHEMA, out_dir))

    odd = foreign_names(out_dir)
    if odd:
        shown = ", ".join(odd[:5])
        more = "" if len(odd) <= 5 else " and %d more" % (len(odd) - 5)
        raise SystemExit(
            "This folder holds no dashboard pages and holds other things (%s%s):\n  %s\n"
            "Nothing was written. Name the dashboard folder itself." % (shown, more, out_dir))

    # No pages here, so nothing in the folder proves what it is. Its name does.
    if not os.path.basename(str(out_dir).rstrip("/\\")).lower().endswith(
            DASHBOARD_FOLDER_NAME.lower()):
        raise SystemExit(
            "This folder holds no dashboard pages, and its name does not end in %s:\n  %s\n"
            "Nothing was written. Pass the dashboard folder, the %s folder inside "
            "Monthly Monitor." % (DASHBOARD_FOLDER_NAME, out_dir, DASHBOARD_FOLDER_NAME))


def cloud_url(_path):
    """The local builder does not infer a cloud address from a filesystem path."""
    return None


def num(value):
    """A finite float, or None. Strings that carry a number are accepted; NaN and
    infinity become None so no non-JSON value can reach a page."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def month_iso(label):
    """'Jun 2026' -> '2026-06'. An ISO month passes through. Anything else -> None."""
    text = str(label or "").strip()
    if re.match(r"^\d{4}-\d{2}$", text):
        return text
    hit = re.match(r"^([A-Za-z]{3})[a-z]*\s+(\d{4})$", text)
    if not hit:
        return None
    mon = _MONTHS.get(hit.group(1).title())
    return "%s-%02d" % (hit.group(2), mon) if mon else None


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# reading verified published runs
# ---------------------------------------------------------------------------

class Deal(object):
    """Everything one selected borrower-month offers before page shaping."""

    def __init__(self, folder, root):
        self.folder = folder
        self.dir = os.path.join(root, "borrowers", folder)
        self.config = None
        self.deal_context = None
        self.period = None
        self.memo_path = None
        self.workbook_path = None
        self.modules = {}
        self.run = None
        self.unreadable = None


def _manifest_path(run_folder, manifest, rel):
    """Resolve one manifest-listed relative path inside its selected run."""
    if not rel or rel not in (manifest.get("files") or {}):
        return None
    parts = str(rel).replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("manifest role does not name a safe run file: %r" % rel)
    path = os.path.realpath(os.path.join(run_folder, *parts))
    if os.path.commonpath((os.path.realpath(run_folder), path)) != os.path.realpath(run_folder):
        raise ValueError("manifest role escapes its selected run: %r" % rel)
    return path


def _role_path(run_folder, manifest, role):
    return _manifest_path(run_folder, manifest, (manifest.get("roles") or {}).get(role))


def _load_run_deal(deal, run_folder, period, run_id):
    """Fill in `deal` from one verified immutable run folder. Shared by the
    accepted selection, a draft preview, and each prior accepted period --
    each caller has already proven `run_folder` is the one verified run it
    claims to be; this only shapes its contents for the page builders."""
    manifest = run_lib.read_manifest(run_folder)
    if manifest is None:
        raise ValueError("selected run has no readable manifest: %s" % run_folder)
    with open(os.path.join(run_folder, run_lib.MANIFEST_NAME), "rb") as fh:
        manifest_bytes = fh.read()
    deal.period = period
    deal.run = {
        "borrower_key": deal.folder,
        "period": period,
        "run_id": run_id,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }

    config_path = _role_path(run_folder, manifest, "config")
    if not config_path:
        raise ValueError("selected run manifest has no config role: %s" % run_folder)
    deal.config = read_json(config_path)
    if deal.config is None:
        raise ValueError("selected run config role is not readable JSON: %s" % config_path)
    context_path = (_role_path(run_folder, manifest, "deal_context")
                    or _manifest_path(run_folder, manifest, "config/deal-context.json"))
    deal.deal_context = read_json(context_path) if context_path else None

    found = {}
    for rel in sorted((manifest.get("files") or {})):
        if not rel.startswith("_archive/") or rel.endswith(".rows.json"):
            continue
        hit = _ARCHIVE_NAME.match(os.path.basename(rel))
        if not hit or hit.group("period") != deal.period:
            continue
        module = hit.group("module")
        if module not in MODULES:
            continue
        found[module] = ("skipped" if hit.group("skipped") else "data",
                         _manifest_path(run_folder, manifest, rel))

    for module in MODULES:
        state, path = found.get(module, ("missing", None))
        payload = read_json(path) if path else None
        if path and payload is None:
            state = "unreadable"
        deal.modules[module] = {"state": state, "data": payload, "path": path}

    deal.memo_path = _role_path(run_folder, manifest, "memo")
    deal.workbook_path = _role_path(run_folder, manifest, "financial_workbook")

    if deal.modules["covenant-compliance"]["state"] not in ("data", "skipped") \
            and deal.modules["three-statement-analysis"]["state"] != "data":
        deal.unreadable = ("This month could not be read: neither the covenant check "
                           "nor the three-statement analysis produced a result.")
    return deal


def collect_published(root, folder):
    """Read one borrower's latest verified selected run into a Deal."""
    deal = Deal(folder, root)
    current = os.path.join(deal.dir, ".record", "current")
    selected = run_lib.latest_published(current)
    if selected is None:
        deal.config = read_json(os.path.join(current, "config", "borrower-config.json"))
        deal.unreadable = "No published monitoring run is available for this borrower yet."
        return deal
    return _load_run_deal(deal, selected["folder"], selected["period"], selected["run_id"])


def collect_draft(root, folder):
    """The currently visible DRAFT for this borrower, verified against its own
    immutable run, or None when there is nothing to preview right now --
    no draft exists, or the visible result is already the accepted one."""
    deal_dir = os.path.join(root, "borrowers", folder)
    current = os.path.join(deal_dir, ".record", "current")
    manifest = run_lib.read_manifest(run_lib.record_dir(deal_dir))
    if manifest is None or manifest.get("status") != "draft":
        return None
    found = run_lib.verified_run(current, manifest.get("period"), manifest.get("run_id"))
    return _load_run_deal(Deal(folder, root), found["folder"], found["period"], found["run_id"])


def history_periods(root, folder, exclude=None):
    """Every OTHER accepted period on record for this borrower, oldest first --
    read from `published.json`'s own period keys, never directory order."""
    deal_dir = os.path.join(root, "borrowers", folder)
    months = run_lib.read_published(deal_dir).get("months") or {}
    out = []
    for period in sorted(months):
        if period == exclude:
            continue
        entry = months[period]
        run_id = entry.get("run") if isinstance(entry, dict) else None
        if run_id:
            out.append((period, str(run_id)))
    return out


def collect_history(root, folder, period, run_id):
    """One borrower's OTHER accepted period, verified on its own with the same
    acceptance proof `collect_published` requires for the current one -- a
    broken or unresolved older link is refused, never silently dropped."""
    deal_dir = os.path.join(root, "borrowers", folder)
    current = os.path.join(deal_dir, ".record", "current")
    found = run_lib.published_run(current, period, run_id)
    return _load_run_deal(Deal(folder, root), found["folder"], found["period"], found["run_id"])


def is_borrower(path):
    """An onboarded borrower, including one with no published run yet."""
    return os.path.isfile(os.path.join(
        path, ".record", "current", "config", "borrower-config.json"))


def discover(root):
    """Every borrower in the OST store, read in key order."""
    borrowers = os.path.join(root, "borrowers")
    if not os.path.isdir(borrowers):
        return []
    out = []
    for name in sorted(os.listdir(borrowers)):
        path = os.path.join(borrowers, name)
        if os.path.isdir(path) and not name.startswith((".", "_")) and is_borrower(path):
            out.append(collect_published(root, name))
    return out


# ---------------------------------------------------------------------------
# shaping: covenants
# ---------------------------------------------------------------------------

def basis_label(window):
    """The basis a covenant is tested on, in the words the credit team uses."""
    if isinstance(window, dict):
        months = window.get("custom_months")
        if months:
            return "%s-month" % int(months)
        return "custom"
    text = str(window or "").strip().lower()
    return {"spot": "spot", "ttm": "LTM", "t3m": "3-month",
            "ytd": "YTD", "": "n/a", "none": "n/a"}.get(text, text.upper())


def covenant_rows(payload):
    """Every covenant test in the output, newest test date first, shaped for a
    page. The module reports one row per covenant per month-end it could test, so
    the grid takes the latest per covenant and the deal page keeps the history."""
    rows = []
    for raw in (payload.get("results") or []):
        rows.append({
            "id": raw.get("id"),
            "name": raw.get("name"),
            "primitive": raw.get("primitive"),
            "test_date": raw.get("test_date"),
            "period": (raw.get("test_date") or "")[:7] or None,
            "basis": basis_label(raw.get("window")),
            "required": num(raw.get("required")),
            "actual": num(raw.get("actual")),
            "headroom_abs": num(raw.get("headroom_abs")),
            "headroom_pct": num(raw.get("headroom_pct")),
            "status": raw.get("status"),
            "source": raw.get("source"),
            "calc": raw.get("calc"),
            "notes": raw.get("notes"),
            "cert_value": num(raw.get("cert_value")),
            "cert_status": raw.get("cert_status"),
            "cert_headroom_pct": num(raw.get("cert_headroom_pct")),
            "delta_model_vs_cert": num(raw.get("delta_model_vs_cert")),
            "cert_agrees": raw.get("cert_agrees"),
            # An either/or covenant: the agreement lets the borrower satisfy one of
            # two tests. The module answers it three times -- once for the pair and
            # once for each leg -- so the page has to know which row is which.
            "either_or_group": raw.get("either_or_group"),
            "group_rule": raw.get("rule") if raw.get("members") else None,
            "group_members": raw.get("members"),
            "group_status": raw.get("group_status"),
            "source_ref": trace_refs(raw.get("source_ref"), payload),
        })
    rows.sort(key=lambda r: (r.get("test_date") or "", r.get("name") or ""), reverse=True)
    return rows


def trace_refs(refs, payload):
    """Reduce source references to the file, sheet, row, and cells a reader can act on."""
    out = []
    files = payload.get("source_files") or []
    for ref in (refs or []):
        if not isinstance(ref, dict):
            continue
        idx = ref.get("file")
        named = None
        if isinstance(idx, int) and 0 <= idx < len(files):
            entry = files[idx]
            named = entry.get("path") if isinstance(entry, dict) else entry
        out.append({
            "path": ref.get("path_in_store") or named,
            "store": ref.get("store"),
            "sheet": ref.get("sheet"),
            "row": ref.get("row"),
            "cells": ref.get("cells"),
            "label": ref.get("label"),
            "section": ref.get("section"),
            "period": ref.get("period"),
            "page": ref.get("page"),
            "quote": ref.get("quote"),
        })
    return out


def latest_per_covenant(rows):
    """One row per covenant -- the most recent test the month produced."""
    seen, out = set(), []
    for row in rows:                                  # already newest-first
        key = row.get("id") or row.get("name")
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def pairs_together(rows):
    """Place each leg of an either/or covenant immediately after its pair."""
    legs = {}
    for row in rows:
        gid = row.get("either_or_group")
        if gid and not row.get("group_members"):
            legs.setdefault(gid, []).append(row)

    out, placed = [], set()
    for row in rows:
        if row.get("either_or_group") and not row.get("group_members"):
            continue                                   # it goes under its pair
        out.append(row)
        placed.add(id(row))
        for leg in legs.get(row.get("id"), []):
            out.append(leg)
            placed.add(id(leg))
    # a leg whose pair the month did not publish keeps its place, at the end
    out.extend(row for row in rows if id(row) not in placed)
    return out


def answers_only(rows):
    """Return one answer per covenant; either/or legs do not count separately."""
    legs = set()
    for row in rows:
        for member in (row.get("group_members") or []):
            legs.add(member)
    return [r for r in rows if r.get("id") not in legs]


def covenant_summary(payload):
    """Summarize covenant counts and the tested covenant with the least room."""
    rows = answers_only(latest_per_covenant(covenant_rows(payload)))
    counts = {"total": len(rows), "pass": 0, "breach": 0, "at_risk": 0, "not_evaluable": 0}
    for row in rows:
        key = row.get("status")
        if key in counts:
            counts[key] += 1

    scored = [r for r in rows if r.get("headroom_pct") is not None
              and r.get("status") in ("pass", "breach", "at_risk")]
    thinnest = min(scored, key=lambda r: r["headroom_pct"]) if scored else None

    dates = [r.get("test_date") for r in rows if r.get("test_date")]
    return {
        "counts": counts,
        "as_of": max(dates) if dates else None,
        "certified_source": payload.get("certified_source"),
        "certified_period": payload.get("certified_period"),
        "thinnest": None if thinnest is None else {
            "id": thinnest.get("id"),
            "name": thinnest.get("name"),
            "basis": thinnest.get("basis"),
            "test_date": thinnest.get("test_date"),
            "headroom_pct": thinnest.get("headroom_pct"),
            "status": thinnest.get("status"),
        },
    }


# ---------------------------------------------------------------------------
# shaping: the trend figures under revenue and EBITDA
# ---------------------------------------------------------------------------

def spark(series, months=SPARK_MONTHS):
    """Copy the last `months` named points for a sparkline without recomputing them."""
    points = []
    for item in (series or []):
        if not isinstance(item, dict):
            continue
        value = num(item.get("v"))
        if value is None:
            continue
        points.append({"m": month_iso(item.get("m")) or item.get("m"), "v": value})
    points = points[-months:]
    if len(points) < 2:
        return None
    values = [p["v"] for p in points]
    return {
        "months": [p["m"] for p in points],
        "values": values,
        "min": min(values),
        "max": max(values),
    }


def metric(trend, series, key):
    """One headline figure with its month-on-month and year-on-year moves, plus
    the sparkline under it."""
    block = (trend or {}).get(key) or {}
    return {
        "latest": num(block.get("latest")),
        "prior": num(block.get("prior")),
        "mom_pct": num(block.get("mom_pct")),
        "yoy_pct": num(block.get("yoy_pct")),
        "direction": block.get("direction"),
        "crosses_basis_break": bool(block.get("crosses_basis_break")),
        "spark": spark((series or {}).get(key)),
    }


def three_statement_summary(payload):
    """The grid's performance cell and the deal page's headline block."""
    trend = payload.get("trend") or {}
    series = payload.get("series") or {}
    liquidity = payload.get("liquidity") or {}
    flags = {"high": 0, "medium": 0, "low": 0}
    for exc in (payload.get("exceptions") or []):
        key = str((exc or {}).get("severity") or "").lower()
        if key in flags:
            flags[key] += 1
    return {
        "latest_month": month_iso(payload.get("latest_month")),
        "metrics": {
            "revenue": metric(trend, series, "revenue"),
            "ebitda": metric(trend, series, "ebitda"),
            "cash": metric(trend, series, "cash"),
        },
        "liquidity": {
            "cash": num(liquidity.get("cash")),
            "burn_1m": num(liquidity.get("burn_1m")),
            "burn_3m_avg": num(liquidity.get("burn_3m_avg")),
            "cash_floor": num(liquidity.get("cash_floor")),
            "floor_source": liquidity.get("floor_source"),
            "months_to_floor_1m": num(liquidity.get("months_to_floor_1m")),
            "months_to_floor_3m": num(liquidity.get("months_to_floor_3m")),
        },
        "headlines": [str(h) for h in (payload.get("headlines") or [])][:6],
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# shaping: the workbook
# ---------------------------------------------------------------------------

def cell_kind(number_format):
    """A cell's number format reduced to how the page must print it. Excel writes
    a growth row as 0.0229…; printed without its format it reads as a dollar
    amount, which is how a reader mistakes 2.3% growth for $0.02."""
    fmt = str(number_format or "").lower()
    if "%" in fmt:
        return "pct"
    if "x" in fmt.replace("xlsx", ""):
        return "mult"
    if "$" in fmt or "#,##" in fmt or "0,0" in fmt:
        return "money"
    if fmt.startswith(("d", "m", "y")) or "yy" in fmt:
        return "date"
    return "num"


def workbook_pages(path, max_rows=400, max_cols=60):
    """Copy workbook cells for reading beside the memo.

    A cell keeps its computed value and number format, never its formula. The
    local workbook remains the record; this page is a readable copy."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        return None, "openpyxl is not installed, so the workbook sheets were not built."

    try:
        book = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:                                  # a locked or partial file
        return None, "The workbook could not be opened (%s)." % type(exc).__name__

    sheets = []
    try:
        for sheet in book.worksheets:
            if sheet.sheet_state != "visible":
                continue
            rows, widest = [], 0
            for r_idx, raw in enumerate(sheet.iter_rows(max_row=max_rows, max_col=max_cols),
                                        start=1):
                cells, kinds = [], []
                for cell in raw:
                    value = cell.value
                    if value is None:
                        cells.append(None)
                    elif isinstance(value, (int, float)) and not isinstance(value, bool):
                        cells.append(num(value))
                        kinds.append(cell_kind(cell.number_format))
                    elif isinstance(value, (_dt.datetime, _dt.date)):
                        cells.append(value.isoformat()[:10])
                    else:
                        cells.append(str(value))
                while cells and cells[-1] is None:
                    cells.pop()
                if cells:
                    widest = max(widest, len(cells))
                row = {"r": r_idx, "c": cells}
                if kinds:
                    # one format per row: these models format a line consistently
                    # across its months, and per-cell formats triple the page size
                    row["k"] = max(set(kinds), key=kinds.count)
                rows.append(row)
            while rows and not rows[-1]["c"]:
                rows.pop()
            sheets.append({"name": sheet.title, "rows": rows, "cols": widest})
    finally:
        book.close()

    if not sheets:
        return None, "The workbook has no readable sheets."
    return sheets, None


# ---------------------------------------------------------------------------
# shaping: pages
# ---------------------------------------------------------------------------

def module_states(deal):
    """Which modules ran, which were skipped, which never wrote -- said plainly,
    because a blank cell and a skipped module are not the same silence."""
    out = {}
    for module, entry in deal.modules.items():
        state = entry["state"]
        note = None
        if state == "skipped":
            payload = entry.get("data") or {}
            note = payload.get("reason") or (payload.get("exception") or {}).get("detail")
        out[module] = {"state": state, "note": note}
    return out


# The house-view fields the dashboard is contracted to show. deal-context's
# our_view can also carry a retired field, a provisional sign-off note, or any
# other analyst annotation never meant to reach a rendered page -- copying the
# block whole put those in front of the artifact's own renderers. Only these
# fields pass through, and only their plain value.
OUR_VIEW_FIELDS = ("watchlist", "trajectory", "thesis_status", "what_would_change_our_mind")


def dashboard_our_view(view):
    """The deal record's `our_view`, narrowed to the fields the dashboard shows.

    This is a field allowlist, not a sign-off check -- it does not evaluate
    whether a field has been signed off (that is `memo_payload.settled_view`'s
    job for the memo). It only keeps the dashboard from ever seeing a field or
    annotation it was not contracted to show.
    """
    view = view or {}
    return {k: view.get(k) for k in OUR_VIEW_FIELDS}


def index_row(deal, files):
    """One borrower's line in the portfolio grid."""
    config = deal.config or {}
    context = deal.deal_context or {}
    view = context.get("our_view") or {}
    static = context.get("static") or {}

    name = (context.get("borrower_name") or config.get("borrower_name")
            or config.get("borrower") or deal.folder)
    short = (config.get("borrower_short_name") or config.get("short_name")
             or re.sub(r"\s+(monitoring|-\s*\d+)\s*$", "", deal.folder,
                       flags=re.I).strip())

    cov = deal.modules.get("covenant-compliance") or {}
    tsa = deal.modules.get("three-statement-analysis") or {}
    cov_sum = covenant_summary(cov["data"]) if cov.get("state") == "data" else None
    tsa_sum = three_statement_summary(tsa["data"]) if tsa.get("state") == "data" else None

    unit = None
    for entry in (cov, tsa):
        if entry.get("state") == "data" and (entry["data"] or {}).get("reporting_unit"):
            unit = entry["data"]["reporting_unit"]
            break

    return {
        "slug": slugify(short or deal.folder),
        "name": name,
        "short": short or deal.folder,
        "folder": deal.folder,
        "period": deal.period,
        "readable": deal.unreadable is None,
        "note": deal.unreadable,
        "unit": unit,
        "our_view": {
            "watchlist": view.get("watchlist"),
            "trajectory": view.get("trajectory"),
        },
        "business": {
            "vertical": static.get("vertical"),
            "one_liner": static.get("business_one_liner"),
        },
        "covenants": cov_sum,
        "performance": tsa_sum,
        "modules": module_states(deal),
        "run": deal.run,
        "files": files,
    }


def deal_page(deal, row):
    """One borrower's own page: every covenant test with its history, the module
    conclusions, and the open items carried into next month."""
    cov = deal.modules.get("covenant-compliance") or {}
    tsa = deal.modules.get("three-statement-analysis") or {}
    context = deal.deal_context or {}

    covenants = covenant_rows(cov["data"]) if cov.get("state") == "data" else []
    cov_data = cov.get("data") or {}
    tsa_data = tsa.get("data") or {}

    return {
        "schema": DEAL_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "generated_at": now_iso(),
        "slug": row["slug"],
        "name": row["name"],
        "short": row["short"],
        "period": deal.period,
        "unit": row["unit"],
        "our_view": dashboard_our_view(context.get("our_view")),
        "business": (context.get("static") or {}),
        "carry_forward": (context.get("carry_forward") or {}),
        "covenants": {
            "latest": pairs_together(latest_per_covenant(covenants)),
            "history": covenants,
            "summary": row["covenants"],
            "cure_rights": cov_data.get("cure_rights"),
            "agreement_ref": cov_data.get("spec_agreement_ref"),
            "certified_source": cov_data.get("certified_source"),
            "transcription_notes": cov_data.get("transcription_notes") or [],
            "exceptions": cov_data.get("exceptions") or [],
            "methodology": cov_data.get("methodology"),
        },
        "performance": {
            "summary": row["performance"],
            "results": tsa_data.get("results") or [],
            "exceptions": tsa_data.get("exceptions") or [],
            "ratio_trends": tsa_data.get("ratio_trends"),
            "revenue_mix": tsa_data.get("revenue_mix"),
            "opex_trends": tsa_data.get("opex_trends"),
            "balance_sheet_yoy": tsa_data.get("balance_sheet_yoy"),
            "methodology": tsa_data.get("methodology"),
        },
        "modules": module_states(deal),
        "files": row["files"],
    }


def workbook_page(deal, row, sheets, note):
    return {
        "schema": WORKBOOK_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "generated_at": now_iso(),
        "slug": row["slug"],
        "name": row["name"],
        "period": deal.period,
        "unit": row["unit"],
        "file_name": os.path.basename(deal.workbook_path) if deal.workbook_path else None,
        "excel_url": cloud_url(deal.workbook_path) if deal.workbook_path else None,
        "note": note,
        "sheets": sheets or [],
    }


# ---------------------------------------------------------------------------
# the narrative page
# ---------------------------------------------------------------------------

def narrative_page(path, period, borrowers):
    """The book-level prose, as the agent wrote it. The engine checks the shape
    and copies the words; it does not write them. A missing or malformed file
    leaves the artifact without a narrative, which the landing page states."""
    raw = read_json(path)
    if raw is None:
        return None, "The narrative file could not be read: %s" % path

    blocks = raw.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return None, "The narrative file has no `blocks` list."

    clean = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        body = block.get("body")
        if not str(body or "").strip():
            continue
        named = [s for s in (block.get("borrowers") or []) if s in borrowers]
        clean.append({
            "heading": str(block.get("heading") or "").strip(),
            "body": str(body).strip(),
            "borrowers": named,
        })
    if not clean:
        return None, "Every block in the narrative file was empty."

    return {
        "schema": NARRATIVE_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "generated_at": now_iso(),
        "period": raw.get("period") or period,
        "written_by": raw.get("written_by"),
        "blocks": clean,
    }, None


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def write_json(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return os.path.getsize(path)


def copy_file(source, target):
    tmp = target + ".tmp"
    shutil.copyfile(source, tmp)
    os.replace(tmp, target)
    return os.path.getsize(target)


def _period_files(out_dir, deal, row, stem, keep_workbooks, written):
    """Write one verified run's memo/deal/workbook pages under `stem`, distinct
    from any other period or draft for the same borrower, and return the
    files map. Shared by the draft preview and each prior accepted period."""
    files = {}
    if deal.memo_path:
        target = os.path.join(out_dir, stem + "--memo.html")
        size = copy_file(resolve(deal.memo_path), target)
        files["memo"] = os.path.basename(target)
        written.append((files["memo"], size))

    deal_name = stem + "--deal.json"
    size = write_json(os.path.join(out_dir, deal_name), deal_page(deal, row))
    files["deal"] = deal_name
    written.append((deal_name, size))

    if keep_workbooks and deal.workbook_path:
        sheets, note = workbook_pages(resolve(deal.workbook_path))
        wb_name = stem + "--workbook.json"
        size = write_json(os.path.join(out_dir, wb_name), workbook_page(deal, row, sheets, note))
        files["workbook"] = wb_name
        files["excel_name"] = os.path.basename(deal.workbook_path)
        written.append((wb_name, size))
    return files


def build(store, out_dir, narrative_path=None, keep_workbooks=True,
          only=None, keep_published=False):
    """Read the store, write every page, and report what happened.

    ``only`` rewrites named borrowers and preserves the other published rows.
    ``keep_published`` permits those preserved rows to use an older build shape;
    otherwise a version mismatch rebuilds the whole book.
    """
    store = resolve(store)
    out_dir = resolve(out_dir)
    if not os.path.isdir(store):
        raise SystemExit("The monitoring folder is not there: %s" % store)
    refuse_wrong_folder(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    deals = discover(store)
    if not deals:
        raise SystemExit("No borrower folders with monitoring output were found in %s" % store)

    rows, report, written = [], [], []

    prior = {}
    if only:
        wanted = {slugify(name) for name in only}
        kept = [d for d in deals if slugify(d.folder) in wanted]
        if not kept:
            raise SystemExit("No borrower folder in %s matches: %s" % (store, ", ".join(only)))
        prior = read_json(os.path.join(out_dir, "index.json")) or {}
        mine = (ml.plugin_version() if ml is not None else None)
        if not prior.get("borrowers"):
            # With nothing to merge into, build the whole book rather than a
            # misleading one-borrower portfolio.
            report.append(("the whole book", "rebuilt",
                           "%s holds no published pages, so every borrower was built rather "
                           "than publishing a book of one." % out_dir))
            prior = {}
            only = None
        elif (prior.get("contract_version") != CONTRACT_VERSION
                or prior.get("plugin_version") != mine):
            # Do not mix row shapes from different plugin builds unless the
            # caller explicitly chooses to preserve them.
            was = prior.get("plugin_version") or "an older version"
            if keep_published:
                report.append((", ".join(only), "rewritten on its own",
                               "The published pages were built by %s and this is %s. Only the "
                               "named borrowers were rewritten; the scheduled refresh brings the "
                               "rest of the book into line." % (was, mine or "this one")))
                deals = kept
            else:
                report.append(("the whole book", "rebuilt",
                               "The published pages were built by %s and this is %s, so every "
                               "borrower was rebuilt rather than mixing two readings."
                               % (was, mine or "this one")))
                prior = {}
                only = None
        else:
            deals = kept
    for deal in deals:
        if deal.period is None:
            row = {
                "slug": slugify(deal.folder), "name": deal.folder, "short": deal.folder,
                "folder": deal.folder, "period": None, "readable": False,
                "note": deal.unreadable, "our_view": {}, "covenants": None,
                "performance": None, "modules": {}, "files": {},
                "run": None, "inputs": {},
            }
            report.append((deal.folder, "unreadable", deal.unreadable))
        else:
            stem = "%s--%s" % (slugify(deal.folder), deal.period)
            files = {}

            if deal.memo_path:
                target = os.path.join(out_dir, stem + "--memo.html")
                size = copy_file(resolve(deal.memo_path), target)
                files["memo"] = os.path.basename(target)
                files["memo_url"] = cloud_url(deal.memo_path)
                written.append((files["memo"], size))

            row = index_row(deal, files)
            row["slug"] = slugify(deal.folder)          # the file stem and the row agree

            deal_name = stem + "--deal.json"
            size = write_json(os.path.join(out_dir, deal_name), deal_page(deal, row))
            files["deal"] = deal_name
            written.append((deal_name, size))

            if keep_workbooks and deal.workbook_path:
                sheets, note = workbook_pages(resolve(deal.workbook_path))
                wb_name = stem + "--workbook.json"
                size = write_json(os.path.join(out_dir, wb_name),
                                  workbook_page(deal, row, sheets, note))
                files["workbook"] = wb_name
                files["excel_url"] = cloud_url(deal.workbook_path)
                files["excel_name"] = os.path.basename(deal.workbook_path)
                written.append((wb_name, size))
                if note:
                    report.append((deal.folder, "workbook", note))

            row["files"] = files
            # What these pages were built from, for stale() to compare against.
            # Recorded after the pages are written, so the fingerprints never
            # appear inside a page -- only in the index row.
            row["inputs"] = deal.run or {}
            report.append((deal.folder, "ok" if row["readable"] else "flagged", row.get("note")))

        # Draft preview: a separately labelled, unaccepted result for this
        # borrower. Never mixed into `files`/`inputs`, which name the
        # accepted selection only, so a new draft never silently replaces
        # the accepted dashboard.
        draft_deal = collect_draft(store, deal.folder)
        if draft_deal is not None:
            draft_row = index_row(draft_deal, {})
            draft_files = _period_files(
                out_dir, draft_deal, draft_row,
                "%s--%s--draft" % (row["slug"], draft_deal.period or "undated"),
                keep_workbooks, written)
            row["draft"] = {"period": draft_deal.period,
                            "run_id": (draft_deal.run or {}).get("run_id"),
                            "files": draft_files}
            report.append((deal.folder, "draft",
                          "a draft for %s is not yet accepted" % draft_deal.period))
        else:
            row["draft"] = None

        # Prior accepted periods, each verified on its own -- never read by
        # directory order, and a broken one fails the whole build rather
        # than being silently dropped from the list.
        history = []
        for period, run_id in history_periods(store, deal.folder, exclude=deal.period):
            hist_deal = collect_history(store, deal.folder, period, run_id)
            hist_row = index_row(hist_deal, {})
            hist_files = _period_files(
                out_dir, hist_deal, hist_row, "%s--%s" % (row["slug"], period),
                keep_workbooks, written)
            history.append({"period": period, "run_id": run_id, "files": hist_files})
        row["history"] = history

        rows.append(row)

    if only:
        merged = {r["slug"]: r for r in (prior.get("borrowers") or [])}
        for row in rows:
            merged[row["slug"]] = row
        report.append(("the rest of the book", "kept",
                       "%d borrowers kept the pages the last refresh wrote."
                       % (len(merged) - len(rows))))
        rows = list(merged.values())

    period = max([r["period"] for r in rows if r["period"]] or [None])
    index = {
        "schema": INDEX_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "generated_at": now_iso(),
        "period": period,
        "source": "credit-monitoring-store",
        "plugin_version": (ml.plugin_version() if ml is not None else None),
        "borrowers": sorted(rows, key=lambda r: (r["short"] or "").lower()),
    }
    if only and prior.get("narrative"):
        index["narrative"] = prior["narrative"]        # a partial refresh leaves the prose alone
    else:
        # A refresh that is not publishing prose KEEPS the prose already in the
        # folder for this month. Without this, any full refresh -- a second
        # borrower finishing, the nightly job, a rebuild for any reason -- took
        # the written month off the dashboard, and the only sign was a link that
        # stopped being there.
        already = "narrative--%s.json" % period
        if period and os.path.isfile(os.path.join(out_dir, already)):
            index["narrative"] = already

    if narrative_path:
        slugs = {r["slug"] for r in rows}
        page, problem = narrative_page(resolve(narrative_path), period, slugs)
        if page is None:
            report.append(("narrative", "missing", problem))
        else:
            name = "narrative--%s.json" % page["period"]
            size = write_json(os.path.join(out_dir, name), page)
            index["narrative"] = name
            written.append((name, size))
            # Say it was taken. Silence read as success, and the only way to
            # know a page had published was to open the folder and look.
            report.append(("narrative", "published",
                           "%s: %d block(s) — %s" % (name, len(page.get("blocks") or []),
                                                     "; ".join(b.get("heading") or "(no heading)"
                                                               for b in (page.get("blocks") or [])))))

    size = write_json(os.path.join(out_dir, "index.json"), index)
    written.append(("index.json", size))
    return index, report, written


def stale(store, out_dir):
    """Name borrowers whose verified selection differs from the current page."""
    store = resolve(store)
    out_dir = resolve(out_dir)
    if not os.path.isdir(store):
        raise SystemExit("The monitoring folder is not there: %s" % store)
    refuse_wrong_folder(out_dir)

    index = read_json(os.path.join(out_dir, "index.json")) or {}
    rows = {row.get("folder"): row for row in index.get("borrowers") or []}
    out = {"stale": [], "in_progress": [], "current": [],
           "period": index.get("period"), "narrative": index.get("narrative")}
    for deal in discover(store):
        row = rows.get(deal.folder)
        page = ((row or {}).get("files") or {}).get("deal")
        if row is None:
            out["stale"].append({"borrower": deal.folder,
                                 "why": "no page has been built yet"})
        elif page and not os.path.isfile(os.path.join(out_dir, page)):
            out["stale"].append({"borrower": deal.folder,
                                 "why": "the page file is missing"})
        elif (row.get("inputs") or {}) != (deal.run or {}):
            out["stale"].append({"borrower": deal.folder,
                                 "why": "the published run changed"})
        else:
            out["current"].append(deal.folder)
    return out


def check(out_dir):
    """Read back what is in the Artifact folder and say whether the artifact can
    open it. Run this after a build, and after a sync, before telling anyone the
    dashboard is ready."""
    out_dir = resolve(out_dir)
    refuse_wrong_folder(out_dir)
    index = read_json(os.path.join(out_dir, "index.json"))
    if index is None:
        raise SystemExit("No readable index.json in %s" % out_dir)
    if index.get("schema") != INDEX_SCHEMA:
        raise SystemExit("index.json is not %s" % INDEX_SCHEMA)

    def _missing(files, label):
        tag = (label + " ") if label else ""
        for kind, name in (files or {}).items():
            if kind.endswith("_url") or kind == "excel_name":
                continue
            if not os.path.isfile(os.path.join(out_dir, name)):
                problems.append("%s: %s%s is named in the index but not in the folder" %
                                (row.get("short"), tag, name))

    problems = []
    for row in index.get("borrowers") or []:
        _missing(row.get("files"), "")
        _missing((row.get("draft") or {}).get("files"), "draft")
        for hist in row.get("history") or []:
            _missing(hist.get("files"), "history %s" % hist.get("period"))
    if index.get("narrative") and not os.path.isfile(os.path.join(out_dir, index["narrative"])):
        problems.append("the narrative page is named in the index but not in the folder")
    return index, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", help="the credit-monitoring-output store")
    ap.add_argument("--out", required=True, help="the Artifact folder the dashboard reads")
    ap.add_argument("--narrative", help="the narrative JSON the agent wrote")
    ap.add_argument("--only", action="append", metavar="BORROWER",
                    help="rewrite just this borrower's pages and leave the rest of the book "
                         "as it is; repeatable")
    ap.add_argument("--keep-published", action="store_true",
                    help="with --only: rewrite exactly the named borrowers and leave every other "
                         "published page as it is, whichever build wrote it")
    ap.add_argument("--no-workbooks", action="store_true",
                    help="skip the workbook sheets (faster; the Excel link still shows)")
    ap.add_argument("--check", action="store_true", help="verify an existing Artifact folder")
    ap.add_argument("--stale", action="store_true",
                    help="name the borrowers whose results are newer than their pages, and stop")
    args = ap.parse_args(argv)

    if args.check:
        index, problems = check(args.out)
        print(json.dumps({"period": index.get("period"),
                          "borrowers": len(index.get("borrowers") or []),
                          "problems": problems}, indent=2))
        return 1 if problems else 0

    if not args.store:
        ap.error("--store is required unless --check is used")

    if args.stale:
        print(json.dumps(stale(args.store, args.out), indent=2))
        return 0

    if args.keep_published and not args.only:
        ap.error("--keep-published names which borrowers to keep to, so it needs --only")

    index, report, written = build(args.store, args.out, args.narrative,
                                   not args.no_workbooks, args.only,
                                   args.keep_published)
    total = sum(size for _, size in written)
    print(json.dumps({
        "period": index.get("period"),
        "refreshed": args.only or "every borrower",
        "borrowers": len(index["borrowers"]),
        "readable": sum(1 for r in index["borrowers"] if r["readable"]),
        "files_written": len(written),
        "bytes_written": total,
        "largest": sorted(written, key=lambda w: -w[1])[:5],
        "notes": [{"borrower": b, "state": s, "note": n} for b, s, n in report if n],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
