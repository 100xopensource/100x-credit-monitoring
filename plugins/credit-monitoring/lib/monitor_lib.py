#!/usr/bin/env python3
"""
monitor_lib -- shared plumbing for the credit portfolio-monitor analysis modules.

Every analysis module (covenant-compliance, budget-vs-actual, ap-ar-aging, ...)
reads the monthly financials model the SAME way through this library, so model
access, number parsing, period math, and output shaping are defined once.

The model is the workbook produced by build-monitoring-model: each financial
statement sheet has its month labels on a header row, column A holds the row
label, and month columns run left to right. The CANONICAL layout (what
build-monitoring-model writes) is header row 6 with "Mon YYYY" labels starting
at column B — but the reader DETECTS the layout per sheet instead of assuming
it, so the known fleet variants (header row 4/6/8; "Mon YYYY" or "YYYY-MM" or
real date cells) all parse with the stock engines, no ad-hoc drivers. Trailing
non-month columns (e.g. "FY2025", "2026 YTD ...") are ignored.

PUBLIC API
==========
Config / paths
    load_config(path) -> dict
        Read a JSON config file.
    parse_store_path(p) -> (store_id, path_in_store) | None
        Read a configured path, which names a shared folder and the path inside
        it: "store:Example Borrower/.record/current/_model/covenant-spec.json".
    store_root(store_id) -> str|None
        Where that shared folder is reachable right now, or None if it is not
        connected here. Found from what is mounted, never from a config.
    store_path(p, must_exist=True) -> str
        A configured path as a path on this machine. Raises ConfigError naming
        the folder when it is not connected — a different problem, and a
        different fix, from a file that is not there.
    resolve_path(p) -> str
        Any path as one that exists on the current machine. A configured
        "<folder>:<path>" goes through store_path; a real path an agent handed
        the engine on the command line is returned as it is, or translated onto
        its sandbox mount by a shared path segment.
    unresolve_path(p) -> str
        The inverse, for source-ref recording: a path on this machine,
        said as "<folder>:<path inside it>" so it means the same thing on
        anyone else's. A path in no connected folder passes through unchanged.

Model access
    open_model(path) -> Model
        Open a model workbook (openpyxl, data_only) and index it. Header row and
        month-label format are detected per sheet (see above).
    Model.wb
        The open openpyxl workbook behind the model — for consumers that need
        cell-level access (original labels, cached values) without a second
        file parse.
    Model.months -> [str]
        Ordered list of month labels present, normalized to canonical "Mon YYYY"
        (e.g. "Jan 2025" ... "May 2026") whatever format the workbook used.
    Model.month_ends -> [str]
        The same months as ISO month-end dates (e.g. "2025-01-31").
    Model.given_path -> str
        The path string the model was opened WITH (as configured, before sandbox
        resolution) — what source refs should record so downstream consumers can
        join store hyperlinks.
    Model.provenance(label, sheet=..., month=None, window="spot") -> dict|None
        Structured per-figure source ref: {sheet, row, label, cells,
        period} for a row read, mirroring get_line's sheet fallback. With month
        (+ window) the cell range covers exactly the cells the value came from.
    Model.get_line(label, month=None, sheet="Income Statement") -> float|None
        Cached value for a row label. With `month` (a label or ISO date) returns
        that month's value; without it returns the whole per-month list. Labels
        are matched case-insensitively and whitespace-normalized against column A.
    Model.windowed(label, month, window, sheet=...) -> float|None
        Resolve a row over a window: "spot" returns that month; "ttm"/"t3m" or an
        int N sum the trailing window (trailing-12 / trailing-3 / trailing-N).
        Returns None if there is not enough trailing history.
    Model.find_row(label_or_regex, sheet=...) -> (matched_label, values) | None
        Find a row by exact/normalized label or, failing that, by regex.

Parsing
    safe_float(x) -> float|None
        nan-string-safe number parser. Returns None for None / "" / "nan" / junk.

Period helpers
    trailing_months(month_label, n) -> [labels]
        The n month labels ending at (and including) month_label, oldest first.
        Pure calendar math, independent of any model.
    to_iso(month_label) -> str
        "Jan 2025" -> "2025-01-01" (first of month).
    month_end_iso(month_label) -> str
        "Jan 2025" -> "2025-01-31" (last day of month).
    month_label(cell) -> str|None
        The ONE month-header reader every engine uses: any cell that names a
        month -> canonical "Mon YYYY", anything else -> None. Accepts real
        date/datetime cells (what openpyxl returns for a date-formatted header),
        "Jan 2025" / "January 2025" / "Jan-2025", "2025-01" / "2025-01-31", and
        the "MM/DD/YYYY" as-of dates some borrower exports use as headers.
    days_in_month(month_label) -> int
        Calendar days in that month — the day basis for every days-metric
        (DSO / DPO / DIO), defined here so two modules can never report the
        same metric on two different bases.

Output
    conclusion(module, period, results, exceptions=None, source_files=None,
               run_id=None, **meta) -> dict
        The one standard JSON conclusion shape every module returns. `period`
        is normalized to the standard ISO "YYYY-MM" stamp; `source_files` is
        the deduped path list per-figure source refs point into; the run stamp
        (run_id / produced_at / produced_by) is added by run_stamp().
    run_stamp(run_id=None, module=None) -> dict
        run_id / run_id_from / produced_at / produced_by — the fields that say
        which run produced an output. The orchestrator mints one id for the
        whole run and passes it to every module (--run-id or CREDIT_MONITOR_RUN_ID); an
        engine run alone mints its own and says so.
    period_stamp(period) -> "YYYY-MM"|None
        Normalize any period spec ("Mon YYYY", ISO date, "YYYY-MM", date, or a
        list of those — latest wins) to the one standard stamp.
    SourceIndex(catalog=None)
        Compact per-file index for source refs: path
        strings are deduped into one `source_files` list; idx.ref(path, **f)
        returns {"file": <position>, "store": ..., "path_in_store": ..., **f}
        for embedding in a figure record. Every added path is normalized through
        unresolve_path, so a sandbox-mount path handed to the engine still
        records as the shared folder plus the path inside it — which is what a
        reader on another machine can open; idx.stores and idx.reach then go in
        the envelope.
    store_of(path, catalog=None) -> (store_id, path_in_store, url_base)
        Where a file sits, said machine-independently, with the URL that path
        is measured from. (None, None, None) when it names no shared folder.

Module-CLI shared helpers (every analysis engine uses these, not local copies)
    ConfigError(ValueError)
        Raised when the borrower-config is missing something a module needs.
    require_line(model_lines, key, module) -> str
        The borrower-config model_lines label a module needs, or ConfigError.
    iter_label_map(d) -> [(name, value)]
        Items of a config label-map, skipping "_"-prefixed comment keys.
    skip_record(module, exc, period=None, borrower=None) -> dict
        The standard skipped=True conclusion for a known bad-input failure.
    write_output(out, path, keep_prior=True, notes=True) -> str
        Resolve the output path, ensure its directory, write indented JSON.
        An answer already there is copied into `_history/`, so a re-run of a
        borrower's month keeps the earlier one (shelve_prior), and the output's
        prose is written beside it as `<name>.md` (render_notes).
    render_notes(out) -> str|None
        The written half of an output as a markdown note — action items first,
        then commentary and what was flagged. None when there is no prose.

Dependencies: openpyxl + Python stdlib only.
"""

import calendar
import glob
import json
import os
import re
import shutil
import time
import uuid
from datetime import date, datetime, timezone
from urllib.parse import quote, unquote, urlsplit

import openpyxl
from openpyxl.utils import get_column_letter
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Layout (the build-monitoring-model workbook convention + detection bounds)
#
# The CANONICAL layout — what build-monitoring-model writes — is header row 6,
# labels in column A, "Mon YYYY" month columns from column B. The reader does
# NOT assume it: per sheet it scans the first HEADER_SCAN_ROWS rows for a run
# of >= MIN_MONTH_RUN consecutive month labels ("Mon YYYY", "YYYY-MM", or real
# date cells) and takes that as the header. HEADER_ROW/FIRST_DATA_COL remain
# as the documented canonical defaults.
# ---------------------------------------------------------------------------
HEADER_ROW = 6          # canonical 1-based header row (detection may differ)
LABEL_COL = 1           # column A holds the row label
FIRST_DATA_COL = 2      # canonical first month column (detection may differ)
HEADER_SCAN_ROWS = 12   # rows scanned per sheet when detecting the header
MIN_MONTH_RUN = 3       # a header needs at least this many consecutive months
                        # (a shorter canonical-shaped run is accepted as a
                        # fallback — see Model._find_header — so a young model
                        # with 1-2 months of history still opens)

# The three canonical statement sheets (the layout contract's exact names).
# They define the model's month axis and must all share it; other sheets that
# happen to carry a month-like run (a notes/dashboard strip) never define or
# contradict the axis.
_STATEMENT_SHEETS = ("Income Statement", "Balance Sheet", "Cash Flow Statement")

# Sandbox path translation. Connected folders are mounted under /sessions/*/mnt/*,
# each named by the leaf of the folder it exposes (e.g. Credit Monitoring, "Example Borrower",
# 2_Executed). Translation matches a mount by its leaf name appearing as a path
# segment in the input -- no borrower or deal name is hardcoded.
_MOUNT_GLOB = "/sessions/*/mnt/*"

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


# ---------------------------------------------------------------------------
# Config errors (shared by every analysis engine)
# ---------------------------------------------------------------------------
class ConfigError(ValueError):
    """Raised when the borrower-config is missing something a module needs.
    A ValueError subclass, so a module's bad-input guard (except ValueError)
    still catches it as a skip condition rather than a crash."""


def require_line(model_lines, key, module):
    """Return the borrower-config model_lines label `key` a module needs, or
    raise ConfigError naming the missing key and the module that needs it."""
    label = (model_lines or {}).get(key)
    if not label:
        raise ConfigError(
            f"model_lines.{key!r} is not set in borrower-config; "
            f"{module} needs it to read the model.")
    return label


def iter_label_map(d):
    """(name, value) pairs of a config label-map, skipping comment keys (any
    key starting with "_"). The config templates ship "_comment" documentation
    keys inside label maps like opex_groups; reading one as a model row would
    inject a phantom group."""
    return [(k, v) for k, v in (d or {}).items() if not str(k).startswith("_")]


# ---------------------------------------------------------------------------
# The reporting unit
#
# Every figure in a borrower's monitoring model is in that model's own unit: one
# deal reports whole dollars, the next reports thousands, and the workbook says
# which in a header no engine reads. Every engine ALSO carries figures stated in
# dollars whatever the model does -- a covenant threshold off the agreement, a
# certified value off the certificate, a materiality floor, an analyst's cash
# floor. Comparing the two sides without converting is wrong by a factor of a
# thousand, and wrong quietly: a $1,000,000 liquidity floor against model cash of
# 2,200 reads as a breach with -997,800 of headroom, and every gate in front of
# it passes clean.
#
# So the unit is declared once per borrower, in borrower-config, and everything
# that mixes the two sides goes through it:
#
#     unit  = ml.reporting_unit(cfg)
#     floor = max(unit.from_dollars(10_000), 0.0005 * annualized)   # comparing
#     text  = f"cash floor ${unit.to_dollars(floor):,.0f}"          # printing
#
# The rule at every site: COMPARE in the model's unit, PRINT in dollars. Model
# figures stay exactly as the model states them, so a memo reads the way the
# workbook does, and any figure a person reads as money is converted back so
# nobody needs to know the scale to check it.
#
# Nothing infers the scale from the size of the numbers. A borrower whose config
# does not declare it raises ConfigError, which every module CLI already reports
# as a skip with a plain reason -- a module that says "tell me the unit" is worth
# far more than one that guesses right most of the time.
# ---------------------------------------------------------------------------
SCALE_NAMES = {1.0: "USD", 1000.0: "thousands of USD",
               1000000.0: "millions of USD"}


class ReportingUnit:
    """What one figure in this borrower's monitoring model stands for.

    scale     -- dollars per model unit: 1 for a model in whole dollars, 1000
                 for a model in thousands. Declared, never inferred.
    currency  -- the ledger's currency, e.g. "USD".
    verbatim  -- what the workbook itself says, e.g. "$ in thousands".
    """

    def __init__(self, scale, currency, verbatim=None):
        self.scale = float(scale)
        self.currency = str(currency).upper()
        self.verbatim = verbatim or None

    @property
    def plain(self):
        """The unit in words, for anything a person reads."""
        named = SCALE_NAMES.get(self.scale)
        if named:
            return named.replace("USD", self.currency)
        return f"units of {self.scale:,.0f} {self.currency}"

    def from_dollars(self, amount):
        """A figure stated in dollars, expressed in this model's unit -- what a
        comparison against a model figure needs."""
        return None if amount is None else float(amount) / self.scale

    def to_dollars(self, value):
        """A figure in this model's unit, expressed in dollars -- what a person
        reading a memo needs."""
        return None if value is None else float(value) * self.scale

    def as_dict(self):
        return {"scale": self.scale, "currency": self.currency,
                "verbatim": self.verbatim, "plain": self.plain}

    def __repr__(self):
        return f"ReportingUnit(scale={self.scale:g}, currency={self.currency!r})"


def reporting_unit(cfg):
    """The unit this borrower's model is denominated in, from borrower-config.

    Raises ConfigError when the config does not declare it, naming what to set.
    """
    model = (cfg or {}).get("model") or {}
    scale, currency = model.get("scale"), model.get("currency")
    if (not isinstance(scale, (int, float)) or isinstance(scale, bool)
            or scale <= 0):
        raise ConfigError(
            f"model.scale is not set in borrower-config (got {scale!r}). Every "
            "module needs it to tell a figure stated in dollars -- a covenant "
            "threshold, a materiality floor -- from a figure in this model's own "
            "unit. Set 1 for a model in whole dollars, 1000 for a model in "
            "thousands, and set model.currency (e.g. \"USD\").")
    if not str(currency or "").strip():
        raise ConfigError(
            "model.currency is not set in borrower-config. Set the ledger's "
            "currency, e.g. \"USD\" -- portfolio answers rank borrowers against "
            "each other and cannot add two currencies.")
    return ReportingUnit(scale, currency, model.get("unit_verbatim"))


THRESHOLD_UNITS = ("dollars", "model_units", "unitless")


def stated_unit(*declared):
    """Which unit a figure a PERSON wrote into a file is stated in.

    The first thing declared wins -- a covenant's own `threshold_unit`, then its
    spec's, or an `analysis` block's. Undeclared means the model's own unit,
    which is what every file written before this field existed means.
    """
    for word in declared:
        if word in THRESHOLD_UNITS:
            return word
    return "model_units"


def on_model_unit(value, unit, stated="model_units"):
    """A figure read out of a file, put on the MODEL's unit -- unchanged unless
    that file says it was written in dollars.

    The other half of the rule `ReportingUnit` exists for: a constant in OUR
    source is dollars and always converts, while a covenant threshold or a
    pinned floor is already on the model's unit unless its file says otherwise.
    """
    if value is None or stated != "dollars":
        return value
    return unit.from_dollars(value)


# ---------------------------------------------------------------------------
# Config / path resolution
# ---------------------------------------------------------------------------
def load_config(path):
    """Read a JSON config file and return the parsed dict."""
    with open(resolve_path(path), "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Store-relative paths — what borrower-config records instead of a machine path
#
# A configured path is written `<store>:<path under that store>`, e.g.
# `store:Example Borrower/.record/current/_model/covenant-spec.json`. The store is one of
# the process-bound `source` or `store` root; the rest is where the file sits
# inside it, in forward slashes.
#
# The point is that nothing in a config names a machine. An absolute path records
# one machine or temporary session. A store id plus a relative path says the
# same thing about the folder without identifying the reader, so another machine
# can resolve it against whatever is connected.
#
# Only a path a config records is written this way. A path an agent hands an
# engine on the command line is a real path it just looked at, and stays one.
# ---------------------------------------------------------------------------
_STORE_PATH = re.compile(r"^(source|store):(.*)$")


def parse_store_path(p):
    """Return a confined `source:` or `store:` logical path, else None."""
    text = str(p or "")
    m = _STORE_PATH.match(text)
    if not m:
        if re.match(r"^[A-Za-z][A-Za-z0-9_]*:(?![\\/])", text):
            raise ConfigError(
                f"unsupported logical root in {p!r}; use source: or store:.")
        return None
    store, raw = m.group(1), m.group(2).strip()
    rest = raw.replace("\\", "/")
    if (not rest or rest.startswith("/") or re.match(r"^[A-Za-z]:", rest)
            or rest in (".", "..") or "/../" in f"/{rest}/"
            or "/./" in f"/{rest}/"):
        raise ConfigError(f"unsafe relative path: {p!r}")
    return store, rest.rstrip("/")


def _descend(base, run):
    """`base` walked down the folder names in `run`, matching case-insensitively,
    or None if any step is missing. Used to reach a store that sits BELOW a
    connected folder, which is what an analyst who shortcut an enclosing folder
    (`1_Deals`, or the whole library) has."""
    here = base
    for want in run:
        if not os.path.isdir(here):
            return None
        hit = next((name for name in os.listdir(here) if name.lower() == want), None)
        if hit is None:
            return None
        here = os.path.join(here, hit)
    return here if os.path.isdir(here) else None


def store_root(store_id, catalog=None):
    """Resolve the only supported logical roots from this process's bindings."""
    env = {"source": "CREDIT_MONITOR_SOURCE_ROOT",
           "store": "CREDIT_MONITOR_STORE_ROOT"}.get(store_id)
    root = os.environ.get(env) if env else None
    return os.path.abspath(root) if root else None


def connected_stores(catalog=None):
    """The currently bound public source and store roots."""
    return {sid: root for sid in ("source", "store")
            for root in (store_root(sid),) if root}


def store_path(p, catalog=None, must_exist=True):
    """A store-relative configured path, as a path on this machine.

    Raises ConfigError naming the store when it is not connected, rather than
    returning something that reads like a missing file -- "connect the folder"
    and "the file is not there" are different problems with different fixes.
    """
    parsed = parse_store_path(p)
    if parsed is None:
        raise ConfigError(
            f"the path {p!r} in borrower-config does not say which shared folder "
            "it is in. Write it as \"<folder>:<path inside it>\", e.g. "
            "\"store:Example Borrower/.record/current/_model/covenant-spec.json\". A full path "
            "starting with a drive letter or /sessions names one person's machine "
            "and does not work on anybody else's.")
    store, rest = parsed
    if store not in ("source", "store"):
        raise ConfigError(
            f"unsupported logical root {store!r}; use source: or store:.")
    root = store_root(store, catalog)
    if root is None:
        raise ConfigError(
            f"the shared folder {store!r} is not connected here, so {rest!r} "
            "cannot be reached. Please connect it and run this again.")
    full = os.path.join(root, *[seg for seg in rest.split("/") if seg]) if rest else root
    if must_exist and not os.path.exists(full):
        raise ConfigError(
            f"{rest!r} is not in the {store!r} folder. Please check the file is "
            "there, or point the setup at the right one.")
    return full


def resolve_path(p):
    r"""Resolve a path to one that exists on the current machine.

    Borrower-agnostic and portable:
      * A CONFIGURED path names its shared folder ("store:Example Borrower/_model/x.json")
        and is resolved against whatever the reader has connected -- see
        store_path. Everything below is for the real path an agent hands an
        engine on the command line, which is a path it just looked at.
      * If `p` already exists on disk -- production on the user's Windows machine,
        or a path already under a /sessions mount -- it is returned unchanged.
      * Otherwise (running in the sandbox against a Windows/OneDrive path), the
        path is translated by MATCHING MOUNTS, not hardcoded roots: each connected
        folder is mounted at /sessions/*/mnt/<leaf>, named by the leaf of the
        folder it exposes (e.g. Credit Monitoring, "Example Borrower", 2_Executed). For each mount we
        look for its leaf name as a path segment in `p`; on a match we remap `p`
        from that segment onward onto the mount and return it if it exists.
      * If nothing matches, `p` is returned unchanged.

    No borrower or deal name is hardcoded.
    """
    if p is None:
        return p
    if parse_store_path(p):
        # Existence is the caller's business, the same as it is below: a module
        # that gates a missing optional input on os.path.exists must keep
        # getting a path back rather than an exception.
        return store_path(p, must_exist=False)
    s = str(p)
    if os.path.exists(s):
        return s
    # Split the input into path segments regardless of separator style.
    segs = [seg for seg in re.split(r"[\\/]+", s) if seg]
    if not segs:
        return s
    for mount in glob.glob(_MOUNT_GLOB):
        if not os.path.isdir(mount):
            continue
        leaf = os.path.basename(mount.rstrip("/"))
        # Match the mount leaf against a path segment (case-sensitive: mount
        # names mirror the connected folder's leaf exactly). Use the LAST match
        # so the most specific (deepest) shared segment wins.
        idx = None
        for i in range(len(segs) - 1, -1, -1):
            if segs[i] == leaf:
                idx = i
                break
        if idx is None:
            continue
        candidate = os.path.join(mount, *segs[idx + 1:]) if idx + 1 < len(segs) else mount
        if os.path.exists(candidate):
            return candidate
    return s


_SANDBOX_PATH = re.compile(r"^[\\/]sessions[\\/][^\\/]+[\\/]mnt[\\/]")


def resolve_output_path(p):
    r"""Resolve a configured OUTPUT path (a file that does NOT exist yet) to a
    writable location, remapping onto a sandbox mount by its DEEPEST existing
    parent directory.

    Unlike resolve_path (for input reads, which correctly leaves a missing path
    unchanged), this must never leave a Windows drive-letter or UNC path
    unchanged -- writing to one in the sandbox would build a literal
    'C:\\Users\\...'-named directory tree in the working directory with an empty,
    unreachable archive. When no mount can host the path it raises ValueError
    rather than write junk.
    """
    if p is None:
        return p
    if parse_store_path(p):
        return store_path(p, must_exist=False)
    s = str(p)
    # Already usable: exists on disk, or already under a sandbox mount.
    if os.path.exists(s) or _SANDBOX_PATH.match(s):
        return s
    segs = [seg for seg in re.split(r"[\\/]+", s) if seg]
    if not segs:
        return s
    lead = os.sep if s[:1] in ("/", "\\") else ""
    # Walk from the full path up to the root; the deepest prefix that resolves to
    # an existing directory (e.g. the connected folder's mount) becomes the base,
    # and the not-yet-existing remainder is grafted on.
    for k in range(len(segs), 0, -1):
        base = resolve_path(lead + os.sep.join(segs[:k]))
        if os.path.isdir(base):
            return os.path.join(base, *segs[k:]) if k < len(segs) else base
    # Nothing resolved. A Windows drive-letter / UNC path with no matching mount
    # must not be written literally (it would build a junk directory tree in the
    # working directory) -- raise, in plain language the analyst can act on.
    if re.match(r"^[A-Za-z]:[\\/]", s) or s.startswith("\\\\") or s.startswith("//"):
        raise ValueError(
            f"couldn't find a connected folder to save this file into ({s!r}). "
            "Please connect the folder you want the results saved to, or point "
            "the setup at a folder that's already connected.")
    return s


def unresolve_path(p, catalog=None):
    r"""The inverse of resolve_path, for source-ref recording: a path
    on this machine, said as "<folder>:<path inside it>".

    A recorded path has to mean something to a reader who is not the person the
    run happened on. `/sessions/<id>/mnt/store/Example Borrower/...` names one session on
    one laptop; `store:Example Borrower/...` names the shared folder and the path inside
    it, which is the same string for everybody.

    A folder is recognized by WHERE it is: every connected store is checked,
    deepest root first, so a file under a store that sits inside another
    connected folder is attributed to the store itself. A path under no
    connected folder is returned unchanged -- it is not somewhere a reader can
    be sent, and saying so is the honest answer.
    """
    if p is None:
        return p
    if parse_store_path(p):
        return str(p)
    s = os.path.abspath(str(p))
    roots = sorted(connected_stores(catalog).items(),
                   key=lambda kv: len(kv[1]), reverse=True)
    for sid, root in roots:
        root_abs = os.path.abspath(root)
        if s == root_abs:
            return f"{sid}:"
        if s.startswith(root_abs.rstrip(os.sep) + os.sep):
            rel = os.path.relpath(s, root_abs).replace(os.sep, "/")
            return f"{sid}:{rel}"
    return str(p)


def store_of(path, catalog=None):
    """Where a source file sits, said in a way that holds on anyone's machine:
    (store_id, path_in_store, url_base) -- the shared folder's catalog id, the
    path inside it, and the URL that path is measured from.
    (None, None, None) when the path names no shared folder.

    A recorded path is otherwise one person's own location: the same borrower
    file reads under one analyst's user folder and another local path for a
    second analyst, so a figure's source note means
    nothing to the second one. The store id plus the path inside the store is
    the same string for everybody.

    The url_base is returned WITH the address because the two share an anchor:
    `path_in_store` is measured from the store folder, so it is the store's own
    URL it hangs off. Both come from the baked catalog, which is why a store
    nobody has connected still links: identifying a folder and reaching it are
    different questions.
    """
    parsed = parse_store_path(unresolve_path(path, catalog))
    if parsed is None:
        return None, None, None
    sid, rel = parsed
    if sid not in ("source", "store"):
        return None, None, None
    return sid, rel, None


# The three file kinds SharePoint opens in an Office viewer, and the prefix each
# viewer answers on. Everything else opens in the document library's own
# preview, which handles a memo's HTML and a spec's JSON.
_OFFICE_VIEWER = {
    ".xlsx": ":x:", ".xlsm": ":x:", ".xlsb": ":x:", ".xls": ":x:", ".csv": ":x:",
    ".docx": ":w:", ".doc": ":w:",
    ".pptx": ":p:", ".ppt": ":p:",
    ".pdf": ":b:",
}


def _path_url(rel, base):
    """The store's `url_base` plus the path inside it, encoded segment by segment.

    The last resort: SharePoint downloads this address rather than opening it, so
    it is used only for a store whose url_base names no site and library, which
    the two viewer addresses are built from.
    """
    url = base.rstrip("/")
    for seg in [s for s in str(rel or "").split("/") if s]:
        url += "/" + quote(seg, safe="")
    return url


def store_link(url_base, path_in_store):
    """The SharePoint address that OPENS a file, from the store's `url_base` and
    the path inside that store. None when there is no url_base.

    A plain path URL to a document library makes the browser fetch the bytes: a
    reader clicking "Open the model in Excel" got a file in their Downloads
    folder, not a workbook on screen. This builds SharePoint's own two viewer
    addresses instead, and neither needs the document's GUID, which a laptop
    cannot know:

      an Office file  https://<host>/:x:/r/sites/<site>/<library>/<path>?web=1
      anything else   https://<host>/sites/<site>/<library>/Forms/AllItems.aspx
                      ?id=<the file's server-relative path>&parent=<its folder>

    Every link in a memo and on the dashboard is built here, from either end: a
    path through `store_url`, or a payload's own store table through the memo
    payload engine. One builder, because the second one is how the download link
    stayed in the memo after the dashboard was fixed.
    """
    base = str(url_base or "").rstrip("/")
    if not base:
        return None
    # https://host + /sites/<site> + /<library> + /<the rest of the base>
    m = re.match(r"^(https?://[^/]+)(/sites/[^/]+)/([^/]+)/?(.*)$", base)
    if not m:
        return _path_url(path_in_store, base)
    host, site, library, inside = m.groups()
    parts = [p for p in inside.split("/") if p]
    parts += [p for p in str(path_in_store or "").split("/") if p]
    if not parts:
        return _path_url(path_in_store, base)

    server_rel = site + "/" + library + "/" + "/".join(parts)
    viewer = _OFFICE_VIEWER.get(os.path.splitext(parts[-1])[1].lower())
    if viewer:
        return host + "/" + viewer + "/r" + quote(server_rel, safe="/") + "?web=1"
    parent = site + "/" + library
    if parts[:-1]:
        parent += "/" + "/".join(parts[:-1])
    return (host + site + "/" + quote(library, safe="") + "/Forms/AllItems.aspx"
            + "?id=" + quote(server_rel, safe="")
            + "&parent=" + quote(parent, safe=""))


def store_url(path, catalog=None):
    """A source path as the SharePoint address that opens the file, or None
    when it is in no known store.

    A None is a contract: the caller shows the file name as plain text, flags it,
    and never emits a guessed link. The address itself is built by `store_link`.
    """
    _, rel, base = store_of(path, catalog)
    if base is None:
        return None
    return store_link(base, rel)


# ---------------------------------------------------------------------------
# Baked store catalog (U1)
#
# The known connected source and store roots ship in config/store-catalog.json, so a folder
# id in a configured path resolves to a real folder and a figure's link is built
# WITHOUT asking the analyst to paste a "Copy link" URL. The catalog names the
# folders and their URLs; where a reader has each one connected is found from
# the mounts (store_root), because local shortcut names are arbitrary and
# renamable. A path in no known folder still works; its links just degrade to
# plain text. url_base is baked because it is NOT derivable from
# the local name (a "... - store" shortcut points at ".../Misc AI Documentation/
# store" in SharePoint).
# ---------------------------------------------------------------------------
_CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "config", "store-catalog.json")


def load_store_catalog(path=None):
    """Load the baked store catalog: the list of known-store dicts (id,
    display_name, local_leaf, url_base, content_marker). An absent or malformed
    catalog -> [] (skills fall back to asking the analyst, never crash). Only
    well-formed entries (non-empty id + display_name + url_base) are returned."""
    p = path or _CATALOG_PATH
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    stores = data.get("stores") if isinstance(data, dict) else None
    if not isinstance(stores, list):
        return []
    return [s for s in stores if isinstance(s, dict)
            and str(s.get("id") or "").strip()
            and str(s.get("display_name") or "").strip()
            and str(s.get("url_base") or "").strip()]


def store_catalog_entry(store_id, catalog=None):
    """The catalog store dict with this id, or None."""
    for s in (catalog if catalog is not None else load_store_catalog()):
        if s.get("id") == store_id:
            return s
    return None


def _store_chains(s):
    """The path-segment runs that identify store `s` inside a local or mounted
    path. Two families: the direct "Add shortcut to OneDrive" leaf name
    (`local_leaf`), and every suffix of the `url_base` folder chain — because an
    analyst who shortcuts an ENCLOSING folder (e.g. 'Misc AI Documentation',
    '1_Deals', or the whole shared credit library) reaches the store beneath the
    shortcut under its REAL SharePoint folder names, so some suffix of the
    url_base tail (always ending at the store's own folder) appears in the
    path. Runs are lowercase segment lists."""
    runs = []
    leaf = [x.lower() for x in re.split(r"[\\/]+", str(s.get("local_leaf") or "")) if x]
    if leaf:
        runs.append(leaf)
    tail = [unquote(seg).lower() for seg in
            urlsplit(str(s.get("url_base") or "")).path.split("/") if seg]
    for i in range(len(tail)):
        runs.append(tail[i:])
    return runs


# ---------------------------------------------------------------------------
# Store-access preflight (U2) — is this path actually usable?
#
# One plain verdict every skill/engine routes access failures through, instead
# of five skills re-deriving "is this cloud-only?" in prose. The read gate
# (ensure_readable) means no engine can parse the bytes of a OneDrive
# Files-On-Demand placeholder — reading one can error or silently truncate
# A not-connected folder is a stop-with-the-connect-steps, never a
# silent fallback.
# ---------------------------------------------------------------------------

# Excel's own workbook-open path limit is 218 chars (stricter than Windows'
# 260, and LongPathsEnabled does not help Excel); 200 leaves headroom.
PATH_BUDGET = 200

# Native-Windows Files-On-Demand attribute: a cloud-only placeholder has
# FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS set (touching it triggers a download).
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000


class StoreAccessError(ValueError):
    """A path can't be used yet — the folder isn't connected, the item isn't
    in it, or the file is a cloud-only placeholder. A ValueError subclass so a
    module's bad-input guard (except ValueError) treats it as a plain-worded
    SKIP, never a crash. Carries the machine verdict and the remedy id (which
    fix steps to show — spelled out in references/connecting-folders.md)."""

    def __init__(self, message, verdict=None, guide=None, path=None):
        super().__init__(message)
        self.verdict = verdict
        self.guide = guide
        self.path = path


def _stat_is_stub(st):
    """True when a stat result describes a OneDrive cloud-only placeholder.
    Two recipes, whichever the platform exposes: native-Windows
    RECALL_ON_DATA_ACCESS attribute, or a POSIX/WSL sparse placeholder
    (size > 0 but zero allocated blocks)."""
    attrs = getattr(st, "st_file_attributes", None)
    if attrs is not None and (attrs & _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS):
        return True
    blocks = getattr(st, "st_blocks", None)
    return blocks is not None and st.st_size > 0 and blocks == 0


def is_cloud_only_stub(path):
    """True when `path` is a OneDrive Files-On-Demand cloud-only placeholder —
    its bytes are not on the device yet, so reading it can error or silently
    truncate. Callers skip it (with the keep-on-device one-liner) rather than parse
    stub bytes. A stat error, a missing file, or a normal/empty file -> False
    (let the normal open raise its own plain error)."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return _stat_is_stub(st)


# Scaffolding a reachable-parent walk must never count as "connected" — these
# exist whether or not the analyst's folder was ever connected, so reaching
# them proves nothing: the sandbox dirs /sessions, /sessions/<id>,
# /sessions/<id>/mnt, and (on a real Windows machine) everything at or above
# the OneDrive root itself — the drive, C:\Users, "OneDrive - <org>".
_SANDBOX_SCAFFOLD = re.compile(r"[\\/]sessions([\\/][^\\/]+([\\/]mnt)?)?[\\/]?")
_ONEDRIVE_ROOT_SEG = re.compile(r"onedrive( - .+)?", re.IGNORECASE)


def _reachable_parent(p):
    """Deepest strict ancestor of the as-configured path that resolves to a
    real folder on this machine, as (resolved_ancestor, first_missing_segment)
    — or (None, None) when no level of the path is reachable. Distinguishes
    'the folder is connected but this item isn't in it' (verdict: missing)
    from 'the folder isn't connected at all' (verdict: not_connected).

    For a OneDrive path, proof of connection starts BELOW the OneDrive root:
    a shortcut that was never added leaves the root reachable and everything
    under it absent, and the right remedy there is the connect steps."""
    s = str(p)
    segs = [seg for seg in re.split(r"[\\/]+", s) if seg]
    lead = s[:1] if s[:1] in ("/", "\\") else ""
    floor = 1
    for i, seg in enumerate(segs):
        if _ONEDRIVE_ROOT_SEG.fullmatch(seg):
            floor = i + 2   # ancestor must include a segment below the root
    for cut in range(len(segs) - 1, floor - 1, -1):
        r = resolve_path(lead + "/".join(segs[:cut]))
        if os.path.isdir(r) and not _SANDBOX_SCAFFOLD.fullmatch(r):
            return r, segs[cut]
    return None, None


def classify_store_access(path):
    """One plain verdict for 'can I actually use this path', as a dict:
        {"verdict": "ok" | "not_connected" | "missing" | "cloud_only_stub",
         "reason": <plain-English line safe to show an analyst>,
         "guide": <guide id or None>,
         "path": <path>}

    * not_connected   — no level of the path is reachable; the folder isn't
                        connected. Remedy: the connect steps (add-shortcut).
    * missing         — the folder IS reachable, but this item isn't in it
                        (not built or delivered yet, or named differently).
                        Treat it as a missing input — skip with a plain
                        reason; the connect steps don't apply.
    * cloud_only_stub — it resolves, but the file is a Files-On-Demand stub.
    * ok              — it resolves to real, hydrated bytes.

    Path-length (Excel's open limit) is a DELIVERED-WORKBOOK concern, handled
    at the delivery step: the build skill checks the workbook's as-configured
    path with path_within_budget and writes a deliver_short_copy when it's
    over (assembling-rules.md). It is not checked here (reads happen through
    the short sandbox mount path), and write_output does not enforce it either
    — its archive JSONs are machine artifacts nobody opens in Excel."""
    p = str(path)
    parsed = parse_store_path(p)
    if parsed is not None:
        # A configured path says which shared folder it is in, so the two
        # failures separate cleanly: the folder is not connected, or the file
        # is not in it. No walking up the path guessing which.
        store, rest = parsed
        root = store_root(store)
        if root is None:
            return {"verdict": "not_connected", "path": p, "guide": "add-shortcut",
                    "reason": (f"I can't reach the '{store}' folder yet — it looks "
                               "like it isn't connected. Please connect it (or add "
                               "its OneDrive shortcut), then run me again.")}
        resolved = store_path(p, must_exist=False)
        if not os.path.exists(resolved):
            return {"verdict": "missing", "path": p, "guide": None,
                    "reason": (f"The '{store}' folder is connected, but I can't "
                               f"find '{rest}' inside it — it may not be created "
                               "or delivered yet, or its name may be spelled a "
                               "little differently.")}
    else:
        resolved = resolve_path(p)
    if not os.path.exists(resolved):
        parent, absent = _reachable_parent(p)
        if parent is not None:
            return {"verdict": "missing", "path": p, "guide": None,
                    "reason": (f"The folder is connected, but I can't find "
                               f"'{absent}' inside it — it may not be created "
                               "or delivered yet, or its name may be spelled "
                               "a little differently.")}
        return {"verdict": "not_connected", "path": p, "guide": "add-shortcut",
                "reason": ("I can't reach this folder yet — it looks like it isn't "
                           "connected. Please connect it (or add its OneDrive "
                           "shortcut), then run me again.")}
    if os.path.isfile(resolved) and is_cloud_only_stub(resolved):
        return {"verdict": "cloud_only_stub", "path": p, "guide": "keep-on-device",
                "reason": ("This file is kept in the cloud only, so its contents "
                           "aren't on this computer yet. Please set its folder to "
                           "'Always keep on this device', let it finish downloading, "
                           "then run me again.")}
    return {"verdict": "ok", "path": p, "guide": None, "reason": "ready"}


def hydrate(path, timeout=90.0, poll=1.5):
    """Last attempt to get a cloud-only file's bytes before giving up on it.

    Reading a placeholder asks OneDrive to fetch it, but only the machine's own
    OneDrive client can act on that, and a sandboxed session cannot count on
    the request reaching it. The real fix is the one-time setup: both folders
    set to "Always keep on this device" with the download finished. The setting
    alone is not the bytes — a file may still be a stub mid-download; the green
    ticks are the signal, not the menu
    checkmark.

    Returns True when the bytes are on the device. A file that is not a stub is
    already True. Reads at most one byte -- the point is to ask for the fetch.
    """
    p = resolve_path(path)
    if not os.path.isfile(p):
        return False
    if not is_cloud_only_stub(p):
        return True
    try:
        with open(p, "rb") as fh:
            fh.read(1)
    except OSError:
        pass
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if not is_cloud_only_stub(p):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.1, float(poll)))


def hydrate_folder(path, limit=400, timeout=90.0):
    """Wake every cloud-only file directly inside `path`. Returns
    (woken, still_asleep) as counts. Bounded by `limit` so a mistaken call on a
    whole library cannot walk forever."""
    root = resolve_path(path)
    woken = asleep = 0
    if not os.path.isdir(root):
        return (0, 0)
    seen = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if seen >= limit:
                return (woken, asleep)
            f = os.path.join(dirpath, name)
            if not is_cloud_only_stub(f):
                continue
            seen += 1
            if hydrate(f, timeout=timeout):
                woken += 1
            else:
                asleep += 1
    return (woken, asleep)


def ensure_readable(path):
    """Gate a file read on classify_store_access: raise StoreAccessError (a
    plain-worded skip) when the path isn't usable, else return the resolved
    on-disk path. open_model and read_spreadsheetml call this so a not-connected
    folder never reaches openpyxl / the XML parser as a traceback.

    A cloud-only file gets ONE wake attempt (hydrate) before refusal — the
    read costs nothing and was about to happen anyway, but it cannot be counted
    on. A file that stays a stub raises with the keep-on-device remedy; the
    caller sends the analyst to the guide's keep-on-device step (see
    connecting-folders.md) instead of retrying."""
    v = classify_store_access(path)
    if v["verdict"] == "cloud_only_stub" and hydrate(path):
        v = classify_store_access(path)
    if v["verdict"] != "ok":
        raise StoreAccessError(v["reason"], verdict=v["verdict"],
                               guide=v["guide"], path=v["path"])
    return resolve_path(path)


def load_source_map(path):
    """The source-map.json build-monitoring-model writes BESIDE the model
    (two-hop trace): per statement sheet, per ISO month column, the
    reporting-package file (as-configured path) that supplied the stored
    values. Returns the parsed dict, or None when the file is missing or
    malformed — a None is a contract: the caller degrades to the model-only
    trace and flags it in the report; it never guesses an upstream file.
    """
    try:
        with open(resolve_path(path), "r", encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(m, dict) or not isinstance(m.get("columns"), dict):
        return None
    return m


# Where a figure sits inside something that is not a spreadsheet. The
# deal folders hold more PDFs than workbooks, plus thousands of Word documents, so
# "sheet + cells" cannot address most of what a credit decision rests on.
#   page    1-based page of a pdf / docx / pptx
#   section the document's own clause or heading ("6.8(a)"), as IT numbers it
#   quote   the wording the figure was read from — the citation that survives a
#           document being re-paginated, amended or re-scanned, and the one an
#           analyst can check without reopening the file
#   line    1-based line of a csv / txt
#   sender / sent  for an email (.msg)
DOC_LOCATION_KEYS = ("page", "section", "quote", "line", "sender", "sent")
QUOTE_MAX = 300


def doc_location(ref):
    """A place inside a document, from whatever shape the caller has.

    Accepts a dict of location fields, or a bare string — which is what the
    covenant specs already hold ("LSA §6.8(a), as amended by Third Amendment §8"):
    prose a person can chase, so it is kept as the `section` rather than dropped.
    Unknown keys are ignored, so a ref cannot smuggle arbitrary fields into an
    address. Returns {} when there is nothing to say.
    """
    if ref is None:
        return {}
    if isinstance(ref, str):
        return {"section": ref.strip()} if ref.strip() else {}
    if not isinstance(ref, dict):
        return {}
    out = {}
    for k in DOC_LOCATION_KEYS:
        v = ref.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        if k in ("page", "line"):
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            if v < 1:
                continue
        elif k == "quote":
            v = " ".join(str(v).split())[:QUOTE_MAX]
        elif isinstance(v, str):
            v = v.strip()
        out[k] = v
    return out


def model_source_map(model_path):
    """The source-map.json build-monitoring-model writes BESIDE a model, loaded
    from the model's own path — so an engine handed a model can resolve the hop
    to the borrower's reporting package without being told where the map is. None when there is no usable map."""
    if not model_path:
        return None
    return load_source_map(os.path.join(os.path.dirname(str(model_path)),
                                        "source-map.json"))


def _iter_iso_months(start, end):
    """Yield ISO YYYY-MM stamps from start to end inclusive."""
    y, mo = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    while (y, mo) <= (ey, em):
        yield f"{y:04d}-{mo:02d}"
        mo += 1
        if mo > 12:
            y, mo = y + 1, 1


def upstream_sources(source_map, sheet, period):
    """The original reporting-package file(s) behind a model figure — the
    second hop of the trace (report figure -> model cell -> package).

    `sheet` + `period` come straight from Model.provenance(): `sheet` is the
    statement sheet name, `period` is "YYYY-MM" or "YYYY-MM..YYYY-MM". Each
    month is looked up in source_map["columns"][sheet]; an entry marked
    {"derived": true} (a derived cash-flow month) is followed ONCE into the
    SAME month's "Income Statement" and "Balance Sheet" entries — the
    statements it was derived from. Returns the distinct entries in month
    order (deduped on file+sheet, each at least {"file": <as-configured
    path>}); [] when the map covers none of it. An empty list is a contract:
    the caller degrades that figure to the model-only trace + an appendix-F
    flag, never a guessed upstream link. Months a partial map does not cover
    are simply skipped — what IS covered still returns.
    """
    if not isinstance(source_map, dict):
        return []
    cols = source_map.get("columns")
    if not isinstance(cols, dict) or not isinstance(cols.get(sheet), dict):
        return []
    period = str(period or "")
    start, _, end = period.partition("..")
    end = end or start
    if not (re.fullmatch(r"\d{4}-\d{2}", start) and re.fullmatch(r"\d{4}-\d{2}", end)):
        return []
    if start > end or int(end[:4]) - int(start[:4]) > 20:
        return []
    out, seen = [], set()

    # The producer may key columns by YYYY-MM or by YYYY-MM-DD (to_iso
    # writes YYYY-MM-01). Index each sheet's map by the YYYY-MM prefix so a
    # YYYY-MM lookup resolves either keying. Cached per sheet.
    idx_cache = {}

    def month_map(s):
        m = idx_cache.get(s)
        if m is None:
            m = {}
            smap = cols.get(s)
            if isinstance(smap, dict):
                for k, v in smap.items():
                    mk = str(k)[:7]
                    if re.fullmatch(r"\d{4}-\d{2}", mk):
                        m.setdefault(mk, v)
            idx_cache[s] = m
        return m

    def add(entry, month, depth):
        if not isinstance(entry, dict):
            return
        if entry.get("derived"):
            if depth == 0:  # follow derived CF once into its source statements
                for s in ("Income Statement", "Balance Sheet"):
                    add(month_map(s).get(month), month, 1)
            return
        f = str(entry.get("file") or "").strip()
        if not f:
            return
        key = (f, str(entry.get("sheet") or ""))
        if key not in seen:
            seen.add(key)
            out.append(entry)

    for month in _iter_iso_months(start, end):
        add(month_map(sheet).get(month), month, 0)
    return out


# ---------------------------------------------------------------------------
# Number parsing
# ---------------------------------------------------------------------------
def safe_float(x):
    """nan-string-safe parser. Returns None for None / "" / "nan" / non-numeric."""
    if x is None:
        return None
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        f = float(x)
        return None if f != f else f  # NaN check
    s = str(x).strip()
    if s == "" or s.lower() in ("nan", "none", "n/a", "na", "-"):
        return None
    s = s.replace(",", "").replace("$", "")
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    try:
        f = float(s)
    except (ValueError, TypeError):
        return None
    if f != f:  # NaN
        return None
    return -f if neg else f


def norm_label(s):
    """Normalize a row/label string for matching: collapse internal whitespace,
    strip, lowercase. The one normalization Model row lookups and the modules'
    label comparisons share. A blank cell normalizes to "" — the same answer as
    an empty string, so a caller testing "is this label blank?" gets a truthful
    answer instead of the word "none".
    """
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip().lower()


# ---------------------------------------------------------------------------
# People — who is one of ours
#
# The memo header names the deal team, and the deal team is the borrower's own
# people. The names in a deal folder are the borrower's: one setup read a
# borrower's CFO out of the documents into the header, on the first line a
# partner reads. Setup infers a name and the renderer prints one, so both ask
# the same two questions here -- does this name carry an officer title, and is
# it the same person as one already on the book.
# ---------------------------------------------------------------------------
# An officer title anywhere in a name. Matched on whole words: "coo" inside
# "Cooper" is a surname, and "vp" inside "Vpalli" is not a role.
OFFICER_TITLE = re.compile(
    r"\b(cfo|ceo|coo|cto|cmo|cro|cao|controller|treasurer|president|founder"
    r"|owner|chairman|chairwoman|vp|svp|evp|head of finance|finance director"
    r"|director of finance|accountant|bookkeeper|manager|partner)\b", re.I)

# Word noise around a person's name: what a document puts before or after it.
_NAME_NOISE = re.compile(
    r"\b(mr|mrs|ms|miss|dr|jr|sr|ii|iii|iv|esq|cpa|cfa|mba)\b", re.I)


def names_an_officer(name):
    """Whether this string carries an officer title, so it is the borrower's."""
    return bool(OFFICER_TITLE.search(str(name or "")))


def person_key(name):
    """`("example", "p")` for "Pat Example", "P. Example" and "EXAMPLE, Pat".

    Surname plus first initial, because files write one person several ways:
    "Pat Example" and "P. Example" are one person, and so are "Sam Sample" and
    "S. Sample". A one-word name keys on itself.
    """
    s = re.sub(r"[^A-Za-z' -]", " ", str(name or ""))
    s = _NAME_NOISE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ("", "")
    if "," in str(name or ""):                 # "Example, Pat" -> "Pat Example"
        last, _, first = s.partition(" ")
        parts = (first + " " + last).split()
    else:
        parts = s.split()
    if len(parts) == 1:
        return (parts[0].lower(), "")
    return (parts[-1].lower(), parts[0][0].lower())


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------
def _parse_month_cell(v):
    """Parse a value that names a month to (year, month_num), else None.

    Accepts every month spelling seen across the fleet's model variants:
    "Jan 2025" / "January 2025" / "Jan-2025", "2025-01" / "2025/01" /
    "2025-01-31" (any day), and real date/datetime cells (openpyxl returns
    datetimes for date-formatted headers). Anything else -> None.
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        return (v.year, v.month)
    s = str(v).strip()
    m = re.match(r"^([A-Za-z]{3,9})[\s\-]+(\d{4})$", s)
    if m:
        mon = m.group(1)[:3].title()
        return (int(m.group(2)), _MONTHS[mon]) if mon in _MONTHS else None
    m = re.match(r"^(\d{4})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?$", s)
    if m:
        mnum = int(m.group(2))
        if 1 <= mnum <= 12:
            return (int(m.group(1)), mnum)
    # "MM/DD/YYYY" as-of dates — some exports head each column with the
    # period's billing / as-of date instead of a month name.
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", s)
    if m:
        mnum, dd = int(m.group(1)), int(m.group(2))
        if 1 <= mnum <= 12 and 1 <= dd <= 31:
            return (int(m.group(3)), mnum)
    # A date-formatted header openpyxl handed back as text, e.g.
    # "2026-05-31 00:00:00".
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ T]", s)
    if m:
        mnum = int(m.group(2))
        if 1 <= mnum <= 12:
            return (int(m.group(1)), mnum)
    return None


def _parse_label(month_label):
    """('Jan 2025' | '2025-01' | ISO date | date) -> (year, month_num).
    Raises ValueError on a non-month label."""
    ym = _parse_month_cell(month_label)
    if ym is None:
        raise ValueError(f"not a month label: {month_label!r}")
    return ym


def _label_from(year, mnum):
    inv = {v: k for k, v in _MONTHS.items()}
    return f"{inv[mnum]} {year}"


def trailing_months(month_label, n):
    """The n month labels ending at (and including) month_label, oldest first."""
    year, mnum = _parse_label(month_label)
    out = []
    for back in range(n - 1, -1, -1):
        y, m = year, mnum - back
        while m <= 0:
            m += 12
            y -= 1
        out.append(_label_from(y, m))
    return out


def to_iso(month_label):
    """'Jan 2025' -> '2025-01-01' (first of the month)."""
    year, mnum = _parse_label(month_label)
    return date(year, mnum, 1).isoformat()


def month_end_iso(month_label):
    """'Jan 2025' -> '2025-01-31' (last day of the month)."""
    year, mnum = _parse_label(month_label)
    last = calendar.monthrange(year, mnum)[1]
    return date(year, mnum, last).isoformat()


def month_label(cell):
    """A header cell that names a month -> canonical 'Mon YYYY'; anything else
    -> None. The one month-header reader the engines share, so a borrower whose
    export heads its columns with real date cells, full month names or as-of
    dates reads the same as one using 'May 2026'."""
    ym = _parse_month_cell(cell)
    return _label_from(ym[0], ym[1]) if ym else None


def days_in_month(month_label_str):
    """Calendar days in the month — the day basis every days-metric (DSO / DPO
    / DIO) multiplies by. One definition, so the aging module and the
    three-statement module cannot report the same metric on two bases."""
    year, mnum = _parse_label(month_label_str)
    return calendar.monthrange(year, mnum)[1]


# ---------------------------------------------------------------------------
# Model access
# ---------------------------------------------------------------------------
class _AmbiguousHeader(ValueError):
    """A sheet carries competing month-label runs and no canonical-position
    header to break the tie (see Model._find_header)."""


class Model:
    """Reads cached values from the monthly financials workbook.

    Per sheet, DETECTS the layout (header row + first month column + label
    format — see _find_header), then builds a map {normalized_label -> [value
    per month column]} plus, for provenance, the 1-based row number and the
    original label text of every row. Labels are matched case-insensitively
    and whitespace-normalized. Month labels are exposed normalized to the
    canonical "Mon YYYY" form whatever format the workbook used, so period
    math downstream is layout-independent.

    A single shared month axis is taken from the first canonical statement
    sheet detected (all statement sheets in the model share the same month
    columns by build contract). A sheet whose detected months do NOT start on
    the axis is never indexed positionally against it: a statement sheet
    raises (loud beats silently wrong-month reads), any other sheet is
    skipped and recorded in `skipped_sheets`. The same loud-or-skip rule
    applies when a sheet's header is ambiguous — competing month runs with
    no canonical-position header to break the tie (see _find_header).
    """

    def __init__(self, path):
        self.given_path = str(path)   # as configured — what source refs record
        # Gate the read: a cloud-only stub or a not-connected folder raises a
        # plain-worded StoreAccessError (a skip), never an openpyxl traceback.
        self.path = ensure_readable(path)
        wb = openpyxl.load_workbook(self.path, data_only=True)
        self.wb = wb               # kept open: cell-level consumers (original
                                   # labels, cached values) read it instead of
                                   # re-parsing the file
        self.months = None         # ordered canonical month labels ("Jan 2025")
        self.month_ends = None     # ordered month-end ISO dates
        self.sheets = {}           # sheet_name -> {norm_label: [values]}
        self.layout = {}           # sheet_name -> {header_row, first_data_col}
        self.skipped_sheets = {}   # sheet_name -> why it was not indexed
        self.duplicate_labels = {} # sheet_name -> [repeated display label, ...]
        self.cross_sheet_labels = {}  # display label -> [sheets carrying it], len > 1
        self._meta = {}            # sheet_name -> {norm_label: (row, orig_label)}
        self._label_index = {}     # norm_label -> (sheet, values, row, orig) first-wins
        axis_start = None

        # Canonical statement sheets first, so the month axis always comes
        # from a real statement when one is detectable — never from a notes /
        # dashboard sheet that happens to carry a month-like strip.
        #
        # In _STATEMENT_SHEETS order, not the workbook's own tab order: this loop
        # also builds _label_index, which is FIRST-WINS and is what get_line falls
        # back to when a label is not on the sheet asked for. Ordering by tab order
        # made that fallback depend on how a borrower happened to arrange its tabs
        # — a label on both the Balance Sheet and the Cash Flow Statement (normal
        # in a three-statement model: "Inventory", "Other current assets" and the
        # like appear as a balance on one and as a change on the other) resolved to
        # whichever tab came first. Same workbook, different tab order, different
        # figure. Precedence is now fixed and stated: Income Statement, then
        # Balance Sheet, then Cash Flow Statement.
        ordered = ([sn for sn in _STATEMENT_SHEETS if sn in wb.sheetnames]
                   + [sn for sn in wb.sheetnames if sn not in _STATEMENT_SHEETS])
        for sn in ordered:
            ws = wb[sn]
            try:
                found = self._find_header(ws)
            except _AmbiguousHeader as e:
                # Competing month axes: reading either would be a guess.
                if sn in _STATEMENT_SHEETS:
                    raise ValueError(
                        f"Sheet '{sn}' has {e} ({self.path})") from None
                self.skipped_sheets[sn] = str(e)
                continue
            if not found:
                continue  # not a financial statement sheet
            header_row, first_col, run = found
            if self.month_ends is None:
                self.months = [_label_from(y, m) for (y, m) in run]
                self.month_ends = [date(y, m, calendar.monthrange(y, m)[1]).isoformat()
                                   for (y, m) in run]
                axis_start = run[0]
                axis_len = len(run)
            elif run[0] != axis_start or len(run) != axis_len:
                # This sheet's months do not line up with the model's axis;
                # indexing it positionally would return the WRONG month's
                # value with no error, and a shorter/longer span would make
                # months the sheet doesn't cover read as fabricated blanks in
                # positional consumers. Same start AND same span is the
                # invariant.
                if sn in _STATEMENT_SHEETS:
                    raise ValueError(
                        f"Sheet '{sn}' month run {_label_from(*run[0])}.."
                        f"{_label_from(*run[-1])} ({len(run)} mo) does not match the "
                        f"model's month axis {self.months[0]}..{self.months[-1]} "
                        f"({axis_len} mo) ({self.path}) — statement sheets must "
                        "share one month axis")
                self.skipped_sheets[sn] = (
                    f"month run {_label_from(*run[0])}..{_label_from(*run[-1])} "
                    f"({len(run)} mo) does not match the axis "
                    f"({axis_len} mo from {self.months[0]}) — not indexed")
                continue
            self.layout[sn] = {"header_row": header_row, "first_data_col": first_col}
            rows, meta = {}, {}
            for r in range(header_row + 1, ws.max_row + 1):
                lab = ws.cell(row=r, column=LABEL_COL).value
                if lab is None:
                    continue
                key = self._norm(lab)
                vals = [ws.cell(row=r, column=first_col + i).value
                        for i in range(len(run))]
                if key in rows:
                    # A repeated normalized label -- the served value stays
                    # last-wins (changing it risks regressions), but record the
                    # collision so it can be surfaced instead of hidden.
                    disp = re.sub(r"\s+", " ", str(lab)).strip()
                    dups = self.duplicate_labels.setdefault(sn, [])
                    if disp not in dups:
                        dups.append(disp)
                rows[key] = vals
                # last-wins, matching rows[key]: provenance must cite the row
                # the served value actually came from when a label repeats
                meta[key] = (r, re.sub(r"\s+", " ", str(lab)).strip())
                self._label_index.setdefault(key, (sn, vals) + meta[key])
            self.sheets[sn] = rows
            self._meta[sn] = meta

        # Labels carried by more than one sheet. The per-sheet duplicate check
        # above cannot see these, and they are NOT a defect — a three-statement
        # model states "Inventory" as a balance on the Balance Sheet and as a
        # change on the Cash Flow Statement by design. They are recorded because
        # they are the labels whose get_line() resolution depends on the sheet
        # precedence above rather than on the caller naming a sheet, which is what
        # a caller reading one of them without a `sheet=` argument is relying on.
        for key in {k for rows in self.sheets.values() for k in rows}:
            on = [sn for sn in self.sheets if key in self.sheets[sn]]
            if len(on) > 1:
                disp = next((self._meta[sn][key][1] for sn in on
                             if key in self._meta.get(sn, {})), key)
                self.cross_sheet_labels[disp] = on

        if self.month_ends is None:
            raise ValueError(
                f"No month-label header detected in the first {HEADER_SCAN_ROWS} "
                f"rows of any sheet in {self.path}")

    def duplicate_label_warning(self):
        """Plain-language warning naming sheets that carry repeated row labels,
        or None. A repeated label means a lookup by that name serves the LAST
        matching row, so the collision is surfaced for a memo to flag rather
        than silently resolved."""
        if not self.duplicate_labels:
            return None
        parts = [f"{sheet} ({', '.join(labels)})"
                 for sheet, labels in self.duplicate_labels.items()]
        return ("The model has more than one row with the same name, so reading a "
                "figure by that name may not pick the row you expect: " + "; ".join(parts))

    # -- header / label parsing -------------------------------------------
    @staticmethod
    def _norm(s):
        return norm_label(s)

    @staticmethod
    def _find_header(ws):
        """Detect the month-label header of a statement sheet.

        Scans rows 1..HEADER_SCAN_ROWS for a run of >= MIN_MONTH_RUN cells that
        (a) each parse as a month ("Mon YYYY", "YYYY-MM", or a real date cell)
        and (b) are CONSECUTIVE calendar months — which is what separates a
        statement's month axis from stray date-like cells or trailing summary
        columns ("FY2025", a fiscal-year-end date, a restarted budget block).

        When several qualifying runs exist they must all describe the SAME
        axis (same first column, same months) — then the canonical-position
        run is returned when present, else the topmost. Runs that disagree
        resolve in three steps. First, trailing blocks fold: a run to the
        right of a strictly longer run on the same row (past an FY/YTD gap
        column) is that row's trailing decoration — the month-shaped cousin
        of the ignored "FY2025"/"YTD" columns — never a competitor; an
        equal-or-longer run to the right is NOT folded (a budget-left/
        actuals-right layout must stay ambiguous, not silently read the left
        block). Second, partial echoes fold: a shorter run BELOW a longer
        same-column run it prefixes is that axis echoed (a period-end date
        row filled only through the last actual), not a competitor — though
        a run at the canonical build position is never folded away. Then the
        run at the canonical build position (HEADER_ROW, FIRST_DATA_COL)
        wins, but ONLY over competitors at or above HEADER_ROW (the
        decoration zone where strips live — picking a strip by length would
        silently misalign every row read). A disagreeing run BELOW the
        canonical position may be the real header of a variant layout, so
        nothing is picked there: _AmbiguousHeader is raised — silent axis
        truncation is worse than a loud refusal.

        Fallback: when no run reaches MIN_MONTH_RUN (a young model with 1-2
        months of history), a shorter run is accepted ONLY in the exact
        canonical shape build-monitoring-model writes — TEXT "Mon YYYY" labels
        starting at FIRST_DATA_COL — so stray date-typed cells in variant
        layouts still cannot masquerade as a header. Short runs resolve like
        the main path: identical months on several rows prefer the canonical
        HEADER_ROW, else the topmost; disagreeing rows resolve to the
        canonical HEADER_ROW or raise — a month-looking title cell above the
        real young header must not truncate the axis.

        Returns (header_row, first_data_col, [(year, month), ...]) or None.
        """
        cands = []  # every qualifying run, in scan order (top row first)
        for r in range(1, min(HEADER_SCAN_ROWS, ws.max_row) + 1):
            c = LABEL_COL + 1  # months never sit in the label column
            while c <= ws.max_column:
                ym = _parse_month_cell(ws.cell(row=r, column=c).value)
                if ym is None:
                    c += 1
                    continue
                run, cc, prev = [], c, None
                while cc <= ws.max_column:
                    got = _parse_month_cell(ws.cell(row=r, column=cc).value)
                    if got is None:
                        break
                    if prev is not None:
                        y, m = prev[0], prev[1] + 1
                        if m > 12:
                            y, m = y + 1, 1
                        if got != (y, m):
                            break  # not the next calendar month — run ends
                    run.append(got)
                    prev = got
                    cc += 1
                if len(run) >= MIN_MONTH_RUN:
                    cands.append((r, c, run))
                c = max(cc, c + 1)
        if cands:
            # Trailing summary blocks fold first: a run to the RIGHT of a
            # strictly longer run on the SAME row — i.e. past an FY/YTD gap
            # column — is that row's trailing decoration (the month-shaped
            # cousin of the ignored "FY2025"/"YTD" columns), never a
            # competitor. An equal-or-longer run to the right is NOT folded:
            # a budget-left/actuals-right layout must stay ambiguous rather
            # than silently read the left block.
            cands = [t for t in cands if not any(
                kr == t[0] and kc < t[1] and len(krun) > len(t[2])
                for (kr, kc, krun) in cands)]
            # Same-axis repeats (a date-typed period-end row under the
            # header, or a decoration strip above it carrying the identical
            # months) are one axis seen N times — prefer the canonical build
            # position when present, else the topmost.
            if len({(c, tuple(run)) for (_r, c, run) in cands}) == 1:
                canon = [t for t in cands
                         if (t[0], t[1]) == (HEADER_ROW, FIRST_DATA_COL)]
                return canon[0] if canon else cands[0]
            # Fold partial echoes first — a shorter run BELOW a longer
            # same-column run it prefixes is that axis echoed through the
            # last actual (a period-end date row), not a competitor. A run at
            # the canonical build position is never folded away.
            keep = []
            for t in sorted(cands, key=lambda t: (-len(t[2]), t[0])):
                r, c, run = t
                if (r, c) != (HEADER_ROW, FIRST_DATA_COL) and any(
                        c == kc and r > kr and krun[:len(run)] == run
                        for (kr, kc, krun) in keep):
                    continue
                keep.append(t)
            if len(keep) == 1:
                return keep[0]
            # The canonical build position is a tie-break against strips
            # ABOVE it (rows 1..HEADER_ROW) — the decoration zone. A
            # disagreeing run BELOW it may be the real header of a variant
            # layout (rows 4/6/8 are all documented), so preferring the
            # canonical run there would silently truncate the axis: that
            # stays ambiguous and fails loudly.
            canon = [t for t in keep
                     if t[0] == HEADER_ROW and t[1] == FIRST_DATA_COL]
            if len(canon) == 1 and all(
                    r <= HEADER_ROW for (r, c, _run) in keep
                    if (r, c) != (HEADER_ROW, FIRST_DATA_COL)):
                return canon[0]
            raise _AmbiguousHeader(
                "competing month-label runs, none at the canonical header "
                f"position (row {HEADER_ROW} starting col "
                f"{get_column_letter(FIRST_DATA_COL)}): " + "; ".join(
                    f"row {r} {get_column_letter(c)}.."
                    f"{get_column_letter(c + len(run) - 1)} = "
                    f"{_label_from(*run[0])}..{_label_from(*run[-1])}"
                    for (r, c, run) in keep))
        # Young-model fallback: canonical-shaped short runs only (see docstring).
        fb = []
        for r in range(1, min(HEADER_SCAN_ROWS, ws.max_row) + 1):
            run, cc, prev = [], FIRST_DATA_COL, None
            while cc <= ws.max_column:
                v = ws.cell(row=r, column=cc).value
                if not (isinstance(v, str)
                        and re.match(r"^[A-Za-z]{3}\s+\d{4}$", v.strip())):
                    break
                got = _parse_month_cell(v)
                if got is None:
                    break
                if prev is not None:
                    y, m = prev[0], prev[1] + 1
                    if m > 12:
                        y, m = y + 1, 1
                    if got != (y, m):
                        break
                run.append(got)
                prev = got
                cc += 1
            if run:
                fb.append((r, FIRST_DATA_COL, run))
        if fb:
            # Identical months on several rows are one axis seen twice (the
            # column is fixed here) — prefer the canonical header row when
            # present, else the topmost, like the main path.
            if len({tuple(run) for (_r, _c, run) in fb}) == 1:
                canon = [t for t in fb if t[0] == HEADER_ROW]
                return canon[0] if canon else fb[0]
            canon = [t for t in fb if t[0] == HEADER_ROW]
            if len(canon) == 1:
                return canon[0]
            raise _AmbiguousHeader(
                "competing short month-label runs, none uniquely at the "
                f"canonical header row {HEADER_ROW}: " + "; ".join(
                    f"row {r} = {_label_from(*run[0])}..{_label_from(*run[-1])}"
                    for (r, _c, run) in fb))
        return None

    # -- month index resolution -------------------------------------------
    def _month_index(self, month):
        """Resolve a month given as a label ('Jan 2025' / '2025-01') or ISO
        date ('2025-01-31' / '2025-01-01') to its column index."""
        if month is None:
            return None
        s = str(month).strip()
        for i, lab in enumerate(self.months):
            if self._norm(lab) == self._norm(s):
                return i
        if s in self.month_ends:
            return self.month_ends.index(s)
        ym = _parse_month_cell(month)
        if ym:
            prefix = f"{ym[0]:04d}-{ym[1]:02d}"
            for i, me in enumerate(self.month_ends):
                if me.startswith(prefix):
                    return i
        return None

    # -- row access -------------------------------------------------------
    def _locate(self, label, sheet=None):
        """(sheet_name, row_number, original_label, values) for a row label,
        or None. Same precedence as _row_values: the named sheet first, else
        the cross-sheet first-wins index."""
        key = self._norm(label)
        if sheet is not None:
            meta = self._meta.get(sheet)
            if meta and key in meta:
                row, orig = meta[key]
                return (sheet, row, orig, list(self.sheets[sheet][key]))
            return None
        hit = self._label_index.get(key)
        if hit:
            sn, vals, row, orig = hit
            return (sn, row, orig, list(vals))
        return None

    def _row_values(self, label, sheet=None):
        """Per-month value list for a row label, or None.

        With `sheet`, look only within that sheet; otherwise use the cross-sheet
        first-wins index (mirrors the original module behavior)."""
        loc = self._locate(label, sheet=sheet)
        return loc[3] if loc else None

    def _axis_series(self, vals):
        """A full-series read always has exactly len(self.months) entries:
        trimmed when the row's sheet ran longer than the axis, None-padded
        when it ran shorter — so positional indexing by the axis can neither
        misalign nor raise, and a padded month reads as an absent cell.
        Month-indexed reads (get_line/windowed/provenance) keep refusing
        months beyond the row's real cells."""
        n = len(self.months)
        out = list(vals[:n])
        if len(out) < n:
            out += [None] * (n - len(out))
        return out

    def get_line(self, label, month=None, sheet="Income Statement"):
        """Cached value for a row label.

        With `month` (label or ISO), returns that month's value as float|None.
        Without `month`, returns the full per-month list of float|None —
        always exactly len(self.months) entries (see _axis_series).

        `sheet` defaults to "Income Statement"; if the label is not on that sheet
        it falls back to the cross-sheet first-wins index, so callers that pass a
        balance-sheet label without naming the sheet still resolve it. That
        precedence is Income Statement, then Balance Sheet, then Cash Flow
        Statement, then any other indexed sheet — fixed, so it does not vary with
        a borrower's tab order. Name the `sheet` for any label the model carries on
        more than one (Model.cross_sheet_labels lists them): a cash-flow "Inventory"
        is a change, not the balance a caller after the balance usually wants."""
        vals = self._row_values(label, sheet=sheet)
        if vals is None:
            vals = self._row_values(label, sheet=None)
        if vals is None:
            return None
        floats = [safe_float(v) for v in vals]
        if month is None:
            return self._axis_series(floats)
        idx = self._month_index(month)
        if idx is None or idx >= len(floats):
            return None
        return floats[idx]

    def windowed(self, label, month, window, sheet="Income Statement"):
        """Resolve a row over a window at `month`.

        window in {"spot","ttm","t3m"} or an int N for a custom trailing-N.
          spot -> the value at `month`
          ttm  -> trailing-12 sum; t3m -> trailing-3 sum; N -> trailing-N sum
        Returns None if `month` is unknown or there is not enough trailing
        history. A missing (None) cell within the window is treated as 0 in the
        sum (matching the original module's summation)."""
        idx = self._month_index(month)
        if idx is None:
            return None
        vals = self._row_values(label, sheet=sheet)
        if vals is None:
            vals = self._row_values(label, sheet=None)
        if vals is None:
            return None
        floats = [safe_float(v) for v in vals]
        if idx >= len(floats):
            return None  # the row's sheet was indexed with fewer months than the axis

        n = _window_n(window)
        if n == 1:
            return floats[idx]
        if idx - (n - 1) < 0:
            return None
        return sum((floats[idx - j] or 0.0) for j in range(n))

    def find_row(self, label_or_regex, sheet=None):
        """Find a row by exact/normalized label, else by regex (search).

        Returns (matched_label, values) or None — values always exactly
        len(self.months) entries (see _axis_series). With `sheet`, searches
        only that sheet; otherwise searches all statement sheets (first match
        wins)."""
        vals = self._row_values(label_or_regex, sheet=sheet)
        if vals is not None:
            return (label_or_regex, self._axis_series(vals))
        try:
            pat = re.compile(label_or_regex, re.IGNORECASE)
        except re.error:
            return None
        sheet_items = ([(sheet, self.sheets.get(sheet, {}))]
                       if sheet is not None else list(self.sheets.items()))
        for _sn, rows in sheet_items:
            for norm_label, values in rows.items():
                if pat.search(norm_label):
                    return (norm_label, self._axis_series(values))
        return None

    def provenance(self, label, sheet="Income Statement", month=None, window="spot"):
        """Structured per-figure source ref: where a row read came from.

        Returns {"sheet", "row", "label", "cells", "period"} or None if the
        label doesn't resolve — or if the read itself would return None
        (month beyond this row's cells, or not enough trailing history for
        the window): a ref is only emitted for a read that could succeed.
        Mirrors get_line's sheet fallback (named sheet, then cross-sheet
        first-wins) so the ref always points at the row the VALUE came from.
        `row` is the 1-based workbook row; `label` is the original
        (un-normalized) row label text.

        `month` + `window` select the cell range exactly as windowed() reads
        it: spot -> the single month cell ("F42"); ttm/t3m/N -> the trailing
        window range ("B42:M42"). Without `month`, cells spans every month
        column of the row. `period` is the ISO YYYY-MM stamp of the month
        read, or "YYYY-MM..YYYY-MM" for a range.
        """
        loc = self._locate(label, sheet=sheet)
        if loc is None and sheet is not None:
            loc = self._locate(label, sheet=None)
        if loc is None:
            return None
        sn, row, orig, vals = loc
        if not vals:
            return None
        first_col = self.layout[sn]["first_data_col"]
        if month is None:
            i0, i1 = 0, min(len(self.months), len(vals)) - 1
        else:
            idx = self._month_index(month)
            if idx is None or idx >= len(vals):
                return None  # mirrors get_line/windowed: month beyond this row's cells
            n = _window_n(window)
            i0, i1 = idx - n + 1, idx
            if i0 < 0:
                return None  # mirrors windowed: not enough trailing history to have read
        c0, c1 = first_col + i0, first_col + i1
        cells = (f"{get_column_letter(c0)}{row}" if c0 == c1
                 else f"{get_column_letter(c0)}{row}:{get_column_letter(c1)}{row}")
        period = (self.month_ends[i1][:7] if i0 == i1
                  else f"{self.month_ends[i0][:7]}..{self.month_ends[i1][:7]}")
        return {"sheet": sn, "row": row, "label": orig,
                "cells": cells, "period": period}


def open_model(path):
    """Open a model workbook and return a Model."""
    return Model(path)


# ---------------------------------------------------------------------------
# Window helper (shared)
# ---------------------------------------------------------------------------
def _window_n(window):
    """Map a window spec to a trailing count. spot=1, t3m=3, ttm=12, int=N."""
    if window == "spot":
        return 1
    if window == "t3m":
        return 3
    if window == "ttm":
        return 12
    if isinstance(window, int):
        return window
    if isinstance(window, dict) and "custom_months" in window:
        return int(window["custom_months"])
    raise ValueError(f"unknown window: {window!r}")


# ---------------------------------------------------------------------------
# Standard output shape
# ---------------------------------------------------------------------------
def recorded_source_path(path, catalog=None):
    """A source-list entry without machine-specific directory state."""
    value = unresolve_path(path, catalog)
    if parse_store_path(value):
        return value
    text = str(value)
    if os.path.isabs(text) or re.match(r"^(?:[A-Za-z]:[\\/]|[\\/]{2})", text):
        shown = text.replace("\\", "/").rsplit("/", 1)[-1]
        return ("unlocated-" + shown.replace(":", "-")) \
            if parse_store_path(shown) else shown
    return text


class SourceIndex:
    """Compact per-file index for module-output source refs.

    Every distinct source path is stored ONCE in the output's top-level
    `source_files` list; per-figure refs point into it by position, so
    thousands of figure refs don't repeat long path strings:

        idx = SourceIndex()
        rec["source_ref"] = [idx.ref(model.given_path,
                                     **model.provenance(label, month=m))]
        ...
        conclusion(..., source_files=idx.files)

    Record every path as its SHARED FOLDER plus the path inside it, not the
    local path the run happened to read, so the downstream artifact can join
    store (SharePoint) hyperlinks onto it and a reader on another machine can
    open it. Engines cannot rely on callers for that — a driving agent in the
    sandbox naturally passes mount paths — so add() normalizes every path
    through unresolve_path, and validate_output.py rejects any machine path
    that still gets through.
    """

    def __init__(self, catalog=None, source_map=None, model_path=None,
                 upstream_map=None):
        self._catalog = catalog if catalog is not None else load_store_catalog()
        self._paths = []
        self._keys = []
        self._pos = {}
        self._stores = {}
        self._reach = {"cell": 0, "file": 0, "unlocated": 0, "borrower_doc": 0}
        self._smap = source_map
        self._model = unresolve_path(str(model_path).strip(), self._catalog) if model_path else None
        self._upstream_map = {unresolve_path(str(k).strip(), self._catalog): v
                              for k, v in (upstream_map or {}).items() if k and v}

    def _address(self, path, **extra):
        """One file as a machine-independent address, honest when it has none.

        The path is said as its shared folder first, the same rule add()
        applies: an upstream path reaches us from a source-map or a config value
        the driving agent may have handed over in sandbox-mount form, and a
        mount path is an address only on the machine that made it.
        """
        path = unresolve_path(path, self._catalog)
        sid, rel, url_base = store_of(path, self._catalog)
        if sid:
            if sid not in self._stores:
                self._stores[sid] = {"url_base": url_base}
            out = {"store": sid, "path_in_store": rel}
        else:
            # No configured store contains it, so there is no address anyone
            # else can open. Say that rather than pass our own path off as one.
            out = {"store": None,
                   "unlocated_reason": "no configured store contains this file"}
        out.update({k: v for k, v in extra.items() if v is not None})
        return out

    def _upstream(self, path, fields, at=None):
        """The BORROWER's own document behind this figure.

        A figure read from the workbook we assemble cites a file we made, which
        is not a document anyone outside the run opens. The model's
        source-map.json already records which reporting package supplied each
        statement sheet and month, and a covenant's threshold comes from the
        signed agreement named in the borrower config — so the hop to the
        borrower's document is resolvable HERE, at the moment the figure is
        produced, instead of being worked out again while a memo is written.
        """
        declared = self._upstream_map.get(path)
        if declared:
            # The caller passes WHERE in the upstream document, because our own
            # ref's `section` is a path inside our JSON ("covenants.min_ebitda"),
            # which says nothing about where the agreement states it.
            return [self._address(declared, **doc_location(at))]
        if not (self._smap and self._model and path == self._model):
            return []
        sheet, period = fields.get("sheet"), fields.get("period")
        if not (sheet and period):
            return []
        out = []
        for entry in upstream_sources(self._smap, sheet, period):
            f = entry.get("file")
            if f:
                out.append(self._address(f, sheet=entry.get("sheet")))
        return out

    def add(self, path):
        """Register a path (deduped after unresolve_path normalization);
        returns its position in .files."""
        key = unresolve_path(str(path).strip(), self._catalog)
        if key not in self._pos:
            shown = recorded_source_path(key, self._catalog)
            if shown in self._paths:
                stem, suffix = os.path.splitext(shown)
                n = 2
                while f"{stem} [{n}]{suffix}" in self._paths:
                    n += 1
                shown = f"{stem} [{n}]{suffix}"
            self._pos[key] = len(self._paths)
            self._keys.append(key)
            self._paths.append(shown)
        return self._pos[key]

    def ref(self, path, upstream_at=None, **fields):
        """A per-figure source ref: the file's position in .files, its address
        inside a shared store, and whatever locating detail the caller passes
        (sheet / row / cells / label / ...).

        `file` alone is not an address — it is a position in a list the figure
        does not travel with, so a figure lifted out of the output points
        nowhere. `store` + `path_in_store` travel WITH the figure and mean the
        same thing on anyone's machine, which is what lets a reader open the
        file a number came from.
        """
        pos = self.add(path)
        out = {"file": pos}
        sid, rel, url_base = store_of(self._keys[pos], self._catalog)
        if sid:
            out["store"] = sid
            out["path_in_store"] = rel
            if sid not in self._stores:
                self._stores[sid] = {"url_base": url_base}
        out.update(fields)
        upstream = self._upstream(self._keys[pos], out, upstream_at)
        if upstream:
            out["upstream"] = upstream
            self._reach["borrower_doc"] += 1
        if out.get("cells") is not None or out.get("row") is not None:
            self._reach["cell"] += 1
        elif out.get("store"):
            self._reach["file"] += 1
        else:
            self._reach["unlocated"] += 1
        return out

    @property
    def files(self):
        return list(self._paths)

    @property
    def stores(self):
        """The stores this output's refs land in: {store_id: {url_base}}. Named
        ONCE in the envelope so a short `store` on each ref is enough to build a
        link, instead of repeating a URL on every figure."""
        return dict(self._stores)

    @property
    def reach(self):
        """How far this output's refs reach: how many name a cell, how many stop
        at a file, how many land in no configured store, and how many reach the
        BORROWER's own document rather than a file we produced. Reported rather
        than left silent — an address that reaches nothing is the failure this
        counts."""
        return dict(self._reach)


def period_stamp(period):
    """Normalize any period spec to the one standard ISO 'YYYY-MM' stamp.

    Accepts 'Mon YYYY' labels, 'YYYY-MM', ISO dates 'YYYY-MM-DD', real
    date/datetime values, or a list/tuple of any of those (the LATEST month
    wins — a module's period is the as-of month of its run). None passes
    through as None (a skip record may not know its period); anything else
    unparseable raises ValueError — a module output must never carry a period
    stamp downstream consumers have to guess at.
    """
    if period is None:
        return None
    if isinstance(period, (list, tuple)):
        stamps = [period_stamp(p) for p in period if p is not None]
        if not stamps:
            return None
        return max(stamps)
    ym = _parse_month_cell(period)
    if ym is None:
        raise ValueError(f"not a period: {period!r}")
    return f"{ym[0]:04d}-{ym[1]:02d}"


def plugin_version():
    """The shipped plugin version, read from the manifest beside this lib.

    None when the manifest is not where the plugin ships it (a bare checkout of
    the engines, a test harness) — a stamp without a version is still a stamp.
    """
    manifest = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            ".claude-plugin", "plugin.json")
    try:
        with open(manifest, encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except (OSError, ValueError):
        return None


def run_stamp(run_id=None, module=None):
    """The fields that say WHICH run produced an output, and when.

    One monthly run dispatches each module as its own process, so an id minted
    per process is a per-MODULE id — the exact bug this closes: it makes one run
    of five modules read as five runs, and ordering two answers for a month then
    falls back to file timestamps. The orchestrator mints the id ONCE and hands
    it to every module (`--run-id`, or the CREDIT_MONITOR_RUN_ID environment variable). An
    engine run on its own mints its own and records `run_id_from: "engine"`, so
    a reader knows this output cannot be grouped with siblings.
    """
    given = run_id or os.environ.get("CREDIT_MONITOR_RUN_ID") or None
    return {
        "run_id": given or "engine-" + uuid.uuid4().hex[:12],
        "run_id_from": "run" if given else "engine",
        "produced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "produced_by": {"engine": module, "plugin_version": plugin_version()},
    }


def conclusion(module, period, results, exceptions=None, source_files=None,
               run_id=None, source_index=None, unit=None, **meta):
    """The one standard JSON conclusion shape every module returns.

    module       -- module name, e.g. "covenant-compliance"
    period       -- period the run covers; normalized to the standard ISO
                    "YYYY-MM" stamp via period_stamp() (a list is accepted —
                    the latest month wins)
    results      -- list of per-test result dicts; every memo-surfaceable
                    figure carries a source_ref (pointing into source_files)
                    and a calc note — validate_output.py enforces this
    exceptions   -- list of exception dicts (breaches / at-risk / errors)
    source_files -- deduped source-path list refs point into (SourceIndex.files)
    run_id       -- the run this output belongs to, minted once by the
                    orchestrator for the whole run; see run_stamp()
    source_index -- the module's SourceIndex; pass this instead of
                    source_files and the envelope also carries `stores` (the
                    url_base per store its refs land in) and
                    `source_ref_reach` (how many refs reach a cell, a file, or
                    nothing) — see SourceIndex.ref
    unit         -- the borrower's ReportingUnit (reporting_unit(cfg)). It lands
                    on the envelope as `reporting_unit`, so every figure in
                    `results` says what it is denominated in without each figure
                    repeating it, and a reader of the archived file never has to
                    open the config. Required of any output carrying results —
                    validate_output enforces that; a skip record has no figures
                    and needs none.
    **meta       -- extra top-level fields (borrower, basis, engine, ...)
    """
    out = {
        "module": module,
        "period": period_stamp(period),
    }
    out.update(run_stamp(run_id, module))
    if unit is not None:
        out["reporting_unit"] = (unit.as_dict() if isinstance(unit, ReportingUnit)
                                 else dict(unit))
    if source_index is not None:
        if source_files is None:
            source_files = source_index.files
        out["stores"] = source_index.stores
        out["source_ref_reach"] = source_index.reach
    out["source_files"] = list(source_files) if source_files is not None else []
    out["results"] = list(results) if results is not None else []
    out["exceptions"] = list(exceptions) if exceptions else []
    for k, v in meta.items():
        out[k] = v
    return out


def skip_record(module, exc, period=None, borrower=None, run_id=None):
    """The one bad-input skip record every module CLI writes when a KNOWN,
    non-bug input failure (missing config surface, unreadable file, unmapped
    month) leaves the module uncomputable: a valid conclusion with
    skipped=True and a plain reason, stamped with the best-known period (None
    when unknown, or when the bad period is itself the reason). Never a
    traceback, never a guessed result.

    A ConfigError carries a message an engine wrote for the analyst who reads
    the memo's data-quality section, so it stands as the reason on its own.
    Every other class is one the engine did not expect, and its name is the
    first thing whoever debugs it needs.
    """
    try:
        stamp = period_stamp(period) if period else None
    except ValueError:
        stamp = None
    reason = str(exc) if isinstance(exc, ConfigError) else f"{type(exc).__name__}: {exc}"
    return conclusion(module=module, period=stamp, results=[], exceptions=[],
                      run_id=run_id, skipped=True, reason=reason,
                      borrower=borrower)


# ---------------------------------------------------------------------------
# Safe writes for synced folders (U3)
#
# OneDrive-synced targets make in-place writes risky: a partial write can leave
# a good file truncated, and delete-then-rename races are documented on this
# runtime class. So write_output writes to a temp file in the SAME directory and
# os.replace()s it into place (atomic on one volume; OneDrive ignores '.tmp').
# ---------------------------------------------------------------------------

# SharePoint/OneDrive-invalid filename characters and reserved device names.
_SHARE_INVALID = set('"*:<>?/\\|')
_SHARE_RESERVED = ({"con", "prn", "aux", "nul"}
                   | {f"com{i}" for i in range(1, 10)}
                   | {f"lpt{i}" for i in range(1, 10)})


def sanitize_share_filename(name):
    """Make `name` safe as a SharePoint/OneDrive-synced filename: replace invalid
    characters (" * : < > ? / \\ |) with '-', collapse whitespace, trim trailing
    dots/spaces, and dodge reserved device names (CON, PRN, ...). Always returns
    a non-empty string."""
    s = "".join("-" if ch in _SHARE_INVALID else ch for ch in str(name))
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    stem = s.rsplit(".", 1)[0] if "." in s else s
    if stem.lower() in _SHARE_RESERVED:
        s = "_" + s
    return s or "untitled"


def path_within_budget(path):
    """True when `path` is short enough for Excel to open it on the analyst's
    machine (<= PATH_BUDGET chars). A delivered workbook whose full path is over
    budget can't be opened; the caller then writes a deliver_short_copy."""
    return len(str(path)) <= PATH_BUDGET


def deliver_short_copy(src_path, shallow_dir, short_name=None):
    """Write a short-named copy of `src_path` into `shallow_dir`, so the analyst
    has a workbook Excel can open when the intended path is over PATH_BUDGET. Returns the copy's path. `short_name` defaults to the source's own
    basename (sanitized); the caller picks a shallow, connected `shallow_dir`."""
    src_resolved = resolve_path(src_path)
    name = sanitize_share_filename(short_name or os.path.basename(str(src_path)))
    dest_dir = resolve_output_path(shallow_dir)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, name)
    shutil.copyfile(src_resolved, dest)
    return dest


_TAG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# The narrative keys an output carries for a PERSON, in the order a reader wants
# them. Rendered to a note beside the JSON so writing meant to be read is
# readable — whether an engine or a worker put it there. `action_items`
# leads: a run once asked the credit team to chase a mis-dated compliance
# certificate and said so only inside an archived JSON, where nobody saw it.
NOTE_SECTIONS = (
    ("action_items", "Action items"),
    ("transcription_notes", "Notes on what was read off the documents"),
    ("headlines", "Headlines"),
    ("comparability_notes", "Comparability"),
    ("analysis_notes", "Analysis notes"),
    # `note` is where several modules put the caveat a reader needs to hold the
    # figures at the right confidence — ap-ar-aging says there that DSO/DPO come
    # off a single month and are volatile, and that the export was scaled.
    ("note", "What to keep in mind reading these figures"),
    ("reason", "Why this was skipped"),
    ("methodology", "How this period's figures were prepared"),
)


def _note_lines(value):
    """A narrative value as markdown bullets: a string is one, a list is many, a
    dict is its values labelled by key. Anything else is skipped rather than
    guessed at."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        out = []
        for v in value:
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
            elif isinstance(v, dict):
                text = v.get("text") or v.get("note") or v.get("detail") or v.get("item")
                if isinstance(text, str) and text.strip():
                    owner = v.get("owner") or v.get("for")
                    out.append(f"**{owner}:** {text.strip()}" if owner else text.strip())
        return out
    if isinstance(value, dict):
        return [f"**{k}:** {v.strip()}" for k, v in value.items()
                if isinstance(v, str) and v.strip()]
    return []


# The measured values an exception carries, in the order a reader compares them.
# A covenant breach is unreadable without them: the covenant name and a test date
# say that it failed, not by how much.
NOTE_FIGURE_KEYS = ("required", "budget", "actual", "aging_total", "balance_sheet")


def _note_figures(e):
    """An exception's measured values as one clause ("required 1,404.00, actual
    -164.00"), in NOTE_FIGURE_KEYS order. Empty when it carries none."""
    parts = []
    for k in NOTE_FIGURE_KEYS:
        v = e.get(k)
        if isinstance(v, bool):
            continue
        label = k.replace("_", " ")
        if isinstance(v, (int, float)):
            parts.append(f"{label} {v:,.2f}")
        elif isinstance(v, str) and v.strip():
            parts.append(f"{label} {v.strip()}")
    return ", ".join(parts)


def render_notes(out):
    """The written half of a module output, as a markdown note.

    An archived output is machine-readable, and the writing in it — commentary,
    what was read off a document, an action item for the credit team — is not
    read by anyone as a result. This renders that writing, and the run's
    exceptions, into a note a person opens. Returns None when the output carries
    no prose worth a file.
    """
    if not isinstance(out, dict):
        return None
    body = []
    for key, heading in NOTE_SECTIONS:
        lines = _note_lines(out.get(key))
        if lines:
            body.append(f"## {heading}\n")
            body.extend(f"- {line}" for line in lines)
            body.append("")

    flagged = [e for e in (out.get("exceptions") or []) if isinstance(e, dict)]
    if flagged:
        # Every module names a flag its own way, so each piece is read from the
        # keys real outputs carry: a covenant writes `covenant`/`name` + `status`
        # + required/actual, a trend flag writes `flag`/`severity`/`detail`, a
        # budget variance writes `metric`/`scope` + budget/actual, an aging
        # tie-out writes `what`/`type` + the two totals. Identical flags collapse
        # into one line naming the dates they happened on — one trend flag repeats
        # unchanged across a whole run, and a straight list is the same sentence
        # thirteen times over.
        groups, order = {}, []
        for e in flagged:
            name = (e.get("flag") or e.get("test_id") or e.get("name")
                    or e.get("covenant") or e.get("metric") or e.get("what")
                    or e.get("section") or "flag")
            kind = e.get("status") or e.get("severity") or e.get("type") or e.get("scope")
            detail = str(e.get("detail") or e.get("note") or e.get("reason")
                         or e.get("error") or e.get("warning") or "").strip()
            figures = _note_figures(e)
            key = (str(name), kind, detail, figures)
            if key not in groups:
                groups[key] = []
                order.append(key)
            when = e.get("test_date") or e.get("date") or e.get("period") or e.get("month")
            if when and str(when) not in groups[key]:
                groups[key].append(str(when))
        body.append("## Flagged this period\n")
        for key in order:
            name, kind, detail, figures = key
            dates = groups[key]
            line = f"- **{name}**" + (f" ({kind})" if kind else "")
            for part in (figures, detail):
                if part:
                    line += f" — {part}"
            if dates:
                line += (f" [{dates[0]}]" if len(dates) == 1
                         else f" [{len(dates)} dates: {dates[0]} to {dates[-1]}]")
            body.append(line)
        body.append("")

    if not body:
        return None

    module = out.get("module") or "module"
    head = [f"# {module} — {out.get('borrower') or 'borrower'}, "
            f"{out.get('period') or 'period'}\n"]
    stamp = [s for s in (out.get("run_id"), out.get("produced_at")) if s]
    if stamp:
        head.append("Run " + " · ".join(str(s) for s in stamp) + "\n")
    if out.get("skipped") is True:
        head.append("**This module was skipped — no figures were produced.**\n")
    return "\n".join(head + body).rstrip() + "\n"


def shelve_prior(out_path):
    """Keep a copy of an existing output in `_history/` before it is replaced, so running a borrower's month again keeps the earlier answer.

    The newest answer keeps the canonical name — every reader that globs
    `_archive/<module>-<YYYY-MM>.json` still finds the current one — and each
    superseded answer lands at `_history/<name>--<run_id>.json`. An answer
    written before the run stamp existed is named by its own modification time
    instead. Returns the history path, or None when there was nothing to keep.

    Copies rather than moves: the caller replaces the canonical file straight
    after, so moving would leave that name absent in between, and a write that
    failed in that window would take the previous answer with it.

    Never raises: keeping history is worth less than the write it precedes.
    """
    if not os.path.exists(out_path):
        return None
    try:
        stem, ext = os.path.splitext(os.path.basename(out_path))
        tag = None
        try:
            with open(out_path, encoding="utf-8") as fh:
                prior = json.load(fh)
            if isinstance(prior, dict):
                tag = prior.get("run_id")
        except (OSError, ValueError):
            pass                     # unreadable or not JSON — fall back to mtime
        if not tag:
            tag = datetime.fromtimestamp(os.path.getmtime(out_path),
                                         timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        hist = os.path.join(os.path.dirname(out_path), "_history")
        os.makedirs(hist, exist_ok=True)
        base = os.path.join(hist, f"{stem}--{_TAG_UNSAFE.sub('-', str(tag))}")
        dest, n = base + ext, 1
        while os.path.exists(dest):
            dest, n = f"{base}-{n}{ext}", n + 1
        shutil.copy2(out_path, dest)
        return dest
    except OSError:
        return None


SKIP_SUFFIX = ".skipped.json"


def counterpart_path(out_path):
    """The OTHER name one module-period answer can carry — an output's skip
    record, or a skip record's output. A module has exactly one current answer
    for a period, under exactly one of the two names."""
    if out_path.endswith(SKIP_SUFFIX):
        return out_path[:-len(SKIP_SUFFIX)] + ".json"
    if out_path.endswith(".json"):
        return out_path[:-len(".json")] + SKIP_SUFFIX
    return None


def retire_counterpart(out_path):
    """Shelve the answer sitting under the OTHER name into `_history/` and clear
    that name, so the month reads as one answer instead of two that disagree.

    A module that skipped and then ran leaves both names populated, and a reader
    that finds outputs by globbing the archive reports the month twice — once
    reported, once skipped for a reason that stopped being true.

    The retired answer is kept the way `shelve_prior` keeps a replaced one, and
    the name is cleared only once that copy is on disk. Returns the history
    path, or None when there was no counterpart. Never raises: the write it
    follows is worth more.
    """
    other = counterpart_path(out_path)
    if not other or not os.path.exists(other):
        return None
    hist = shelve_prior(other)
    if not hist:
        return None
    try:
        os.remove(other)
        note = os.path.splitext(other)[0] + ".md"
        if os.path.exists(note):
            os.remove(note)
    except OSError:
        pass
    return hist


def _write_notes_beside(out, out_path):
    """Write the output's prose to `<same name>.md`. Never raises: the note is
    worth less than the output it accompanies. An output with no prose leaves no
    note, and clears a stale one from a previous run."""
    note_path = os.path.splitext(out_path)[0] + ".md"
    try:
        text = render_notes(out)
        if text:
            with open(note_path, "w", encoding="utf-8") as fh:
                fh.write(text)
        elif os.path.exists(note_path):
            os.remove(note_path)
    except OSError:
        pass


def write_output(out, path, keep_prior=True, notes=True):
    """Resolve an output path to a writable location, ensure its parent
    directory exists, and write `out` as indented JSON ATOMICALLY (temp file in
    the same directory + os.replace). Returns the resolved path. A failed write
    leaves any prior file intact and leaves no residue — never a truncated
    archive. The output-write tail every module CLI shares (each keeps its own
    result/skip print line).

    keep_prior -- an answer already at `path` is copied into `_history/` (see
    shelve_prior), so a re-run adds to the record instead of erasing it. It is
    copied only once the new answer is written and ready to move into place:
    shelving first would empty the canonical name, so a write that then failed
    would leave every reader that globs `_archive/<module>-<period>.json`
    finding nothing.
    notes -- the output's prose is also written as a markdown note beside it
    (see render_notes), so writing meant for a person is readable.

    An answer under the module-period's OTHER name — a skip record when this is
    an output, an output when this is a skip record — is retired to `_history/`
    (see retire_counterpart), so the archive holds one answer per module-period.
    """
    out_path = resolve_output_path(path)
    parent = os.path.dirname(out_path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = out_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        if keep_prior:
            shelve_prior(out_path)
        os.replace(tmp, out_path)   # atomic on one volume; OneDrive-safe
        retire_counterpart(out_path)
        if notes:
            _write_notes_beside(out, out_path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return out_path


# ---------------------------------------------------------------------------
# SpreadsheetML (Excel 2003 XML) reader
# ---------------------------------------------------------------------------
def _sml_local(tag):
    """Local tag name, dropping any '{namespace}' prefix."""
    return tag.rsplit("}", 1)[-1]


def _sml_attr(el, name):
    """Fetch an attribute by LOCAL name (SpreadsheetML attrs are namespaced,
    e.g. '{urn:...:spreadsheet}Index'), so we match on the local part."""
    for k, v in el.attrib.items():
        if _sml_local(k) == name:
            return v
    return None


def read_spreadsheetml(path):
    """Read a SpreadsheetML (Excel 2003 XML, '.xls' that is really XML) workbook
    into {sheet_name: rows}, where each row is a list of cell values (str / float
    / None).

    Honors ss:Index (sparse columns are padded with None so columns stay aligned)
    and ss:MergeAcross (a merged cell is followed by that many None placeholders).
    Numeric <Data ss:Type="Number"> values are returned as float; everything else
    is returned as its text (callers parse numbers with safe_float). This is the
    reader ap-ar-aging relies on for NetSuite-style aging exports.
    """
    # Gate the read like open_model: a cloud-only stub / not-connected path
    # raises a plain skip, never a bare XML-parser error.
    path = ensure_readable(path)
    # SpreadsheetML aging exports are trusted, analyst-provided reporting files (from the
    # borrower's own package), not untrusted external input — the stdlib parser is fine here.
    tree = ET.parse(path)  # nosemgrep: python.lang.security.use-defused-xml-parse.use-defused-xml-parse
    root = tree.getroot()

    sheets = {}
    for ws in root.iter():
        if _sml_local(ws.tag) != "Worksheet":
            continue
        sheet_name = _sml_attr(ws, "Name") or f"Sheet{len(sheets) + 1}"

        table = None
        for child in ws:
            if _sml_local(child.tag) == "Table":
                table = child
                break

        rows = []
        if table is not None:
            for row_el in table:
                if _sml_local(row_el.tag) != "Row":
                    continue
                cells = []
                for cell_el in row_el:
                    if _sml_local(cell_el.tag) != "Cell":
                        continue
                    # ss:Index (1-based) explicitly positions this cell.
                    idx = _sml_attr(cell_el, "Index")
                    if idx is not None:
                        target = int(idx) - 1
                        while len(cells) < target:
                            cells.append(None)
                    # Cell value from its <Data> child.
                    val = None
                    for d in cell_el:
                        if _sml_local(d.tag) != "Data":
                            continue
                        dtype = _sml_attr(d, "Type")
                        text = d.text
                        if text is not None and dtype == "Number":
                            try:
                                val = float(text)
                            except ValueError:
                                val = text
                        else:
                            val = text
                        break
                    cells.append(val)
                    # ss:MergeAcross N -> pad N trailing placeholder columns.
                    merge = _sml_attr(cell_el, "MergeAcross")
                    if merge:
                        try:
                            for _ in range(int(merge)):
                                cells.append(None)
                        except ValueError:
                            pass
                rows.append(cells)
        sheets[sheet_name] = rows
    return sheets
