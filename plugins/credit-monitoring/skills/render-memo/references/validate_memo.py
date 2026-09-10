#!/usr/bin/env python3
"""Refuse a monitoring memo that disagrees with its own inputs.

Exit 0 and "OK", or exit 1 and the list of problems -- the same contract as
validate_model.py, validate_spec.py and validate_output.py. Reads the written HTML
with stdlib html.parser, so no new dependency.

WHY THIS EXISTS
The memo must match its payload: chart data, printed figures, and tables are all
checked against the archived result behind them.

A memo is the only artifact in this plugin a person acts on without opening
anything else, so it gets the same treatment the model build gets: a gate in front
of the folder, and a failure keeps the last good file in place.

NINE CHECKS
  1  every printed figure matches the payload entry it claims -- a statement table
     cell against its own row and column, every other traced figure against the
     run's own numbers
  2  every drawn figure matches the payload's series, and no chart series is
     written into the page's own script
  3  one borrower, one period -- both are named, in the page and in the browser
     title, and no other borrower's name appears
  4  parts foot to their stated total, and a figure carried twice carries one value
  5  nothing is silently omitted -- every `absent` record shows up on the page, and
     every one of the eighteen promised sections either carries content or the run
     says why
  6  coverage -- no token left unfilled in text OR in an attribute, every traceable
     figure answers, nothing traceable is an anchor, markup closes
  7  the box rule -- how a memo with an unwritten text box is treated, set by
     `--empty-boxes allow|warn|refuse`
  8  the flags box covers the run's open items
  9  the five-tab structure is intact, and no deep link points at a missing id

WHAT IT DELIBERATELY DOES NOT FLAG
A prose figure that restates a table cell. Inherited coverage is the format's own
rule ("every fact lives in exactly one place"), so a repeat in a sentence is
correct, not a duplicate.

Usage:
  python validate_memo.py --payload <memo-payload.json> --memo <memo.html>
                          [--empty-boxes allow|warn|refuse]
"""
import argparse
import html
import json
import os
import re
import sys
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "source", "track", "wbr"}
TRACEABLE_CLASSES = {"trc", "srclink", "srcmiss"}
TOKEN = re.compile(r"\{\{([A-Z_0-9]+)\}\}")
# A money cell as the memo writes it: whole dollars, thousands separators, a
# negative in brackets. "1,250" and "(1,250)" and "3.21×" all parse.
MONEY = re.compile(r"^\(?\s*[-+]?[\d,]+(?:\.\d+)?\s*\)?$")
# The leading figure of a cell that carries words after it -- a covenant value with
# its status tag, a year-to-date sum with its coverage note. Without this the gate
# never compares the cells that carry the most judgment.
LEADING_NUMBER = re.compile(r"^\s*\(?\s*[-+−]?\$?\s*[\d,]+(?:\.\d+)?\s*\)?\s*[%xX×]?")
# A figure the way the memo writes one, found anywhere in a container: it carries a
# thousands separator, or a decimal, or a trailing % or ×. A bare year, a row count
# and a clause number never match, so the sweep reads figures and not text.
FIGURE_TOKEN = re.compile(
    r"\(?\s*[-+−]?\$?\s*(?:\d{1,3}(?:,\d{3})+|\d+\.\d+|\d+)\s*\)?\s*[%×xX](?!\w)"
    r"|\(?\s*[-+−]?\$?\s*\d+(?:\.\d+)?\s*MM\b\)?"
    r"|\(?\s*[-+−]?\$?\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*\)?")
# Past this many words a container is a sentence -- a term of the deal, a note --
# and the numbers in it are quoted words rather than figures the run produced.
FIGURE_WORDS = 12
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
DATE_CELL = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|\d{1,2}\s+(?:"
    + "|".join(MONTHS + [month[:3] for month in MONTHS])
    + r")\s+\d{4})\b", re.I)
AGING_RANGE_CELL = re.compile(r"^\d+\s*[-–—]\s*\d+(?:\s+days?)?$", re.I)
NUMBERED_FILENAME_CELL = re.compile(
    r"^\d+_[^/\\\n]+\.[A-Za-z][A-Za-z0-9]{0,7}(?:\s+[—-]\s+.*)?$")
# The five panes, their ids and their order -- fixed so a `#tab=` link is stable
# across borrowers (report-structure "Layout").
TABS = ["tab-overview", "tab-deal", "tab-covenants", "tab-financials", "tab-files"]
# The three statement tables, found by the corner header the renderer writes, and
# the payload table each one has to agree with cell for cell.
STATEMENT_TABLES = {"Income statement": "income_review",
                    "Cash flow": "cashflow_review",
                    "Balance sheet": "balance_review"}
PCT_UNITS = {"%", "pct", "percent"}
RATIO_UNITS = {"ratio", "x", "×"}
# The three boxes with no token of their own: the renderer hands each to the
# producer that builds a table and it fills one cell per row, so an unwritten one
# leaves a marked dash in a row rather than a paragraph on the page.
KEYED_BOXES = {"COVENANT_DELTAS": "the covenant grid's Δ / note column",
               "CAPSTRUCTURE_NOTES": "the capital structure's pricing / notes column",
               "DOCUMENT_NOTES": "the file registry's what-it-is column"}
# The chart's series as the template names them. A memo that declares any of these
# as a literal array is drawing numbers the run never produced. `Q` is the
# quarter boxes: the template reads them from the island, so a literal there is the
# same defect wearing a different variable.
CHART_VARS = ["M", "REV", "GP", "OPEX", "EB", "GPM", "EBM", "OPXM", "Q"]
# The worked memo example, which the shipped template replaces. Its name on a page
# means some of its data is still there.
EXAMPLE_BORROWERS = ["Example Borrower"]
# An open item written as an instruction leads with the ask, not with its subject.
INSTRUCTION_WORDS = {"confirm", "reconcile", "request", "provide", "supporting",
                     "obtain", "clarify", "chase", "deliver", "itemized", "priority",
                     "review", "verify", "explain", "identify"}


class Memo(HTMLParser):
    """The memo, read the way a reader meets it: tabs, tables, cells, traces, text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []              # (tag, traced-span record or None)
        self.balance = []
        self.text_parts = []
        self.cells = []              # every td/th, with its text and whether it traces
        self.traces = []             # every trace span: (classes, calc, hops, href, title)
        self.spans = []              # the same spans with their printed text and place
        self.tokens_left = set()
        self.tokens_in_attrs = set()
        self.anchors_in_traceable = 0
        self.scripts = {}            # id -> raw text of a JSON island
        self.script_text = []        # every ordinary <script>, for the literal sweep
        self.ids = set()
        self.anchor_targets = []     # every in-page href="#..." target
        self.panes = []              # (id, is_active) per .tabpane
        self.tab_buttons = []        # each button's data-tab
        self.boxes = []              # each data-box marker's box name
        self.tables = []             # {corner, rows: [{label, cells}]}
        self.title = ""
        self.trace_parts = []        # what a reader gets when they open a trace box
        self.flags_text = None       # the text of the Flags & follow-ups list
        self.raw = ""                # the file as written
        self._script_id = None
        self._script_buf = []
        self._in_script = False
        self._cell = None
        self._cell_traced = False
        self._cell_span = False
        self._row_traced = False
        self._table = None
        self._row = None
        self._in_head_row = False
        self._in_title = False
        self._seen_flags_heading = False
        self._flags_buf = None

    # -- structure
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        # A token left in an ATTRIBUTE is invisible to a text scan and just as
        # unfilled: a `data-href="{{...}}"` is a link to nowhere.
        for key, val in attrs:
            for m in TOKEN.finditer(val or ""):
                self.tokens_left.add(m.group(1))
                self.tokens_in_attrs.add(f"{m.group(1)} (in the {tag} {key})")
        if a.get("id"):
            self.ids.add(a["id"])
            if a["id"] == "sec-flags":
                self._seen_flags_heading = True
        cls = set((a.get("class") or "").split())
        href = a.get("href") or ""
        if tag == "a" and href.startswith("#") and len(href) > 1:
            self.anchor_targets.append(href[1:])
        if "tabpane" in cls:
            self.panes.append((a.get("id"), "active" in cls))
        if tag == "button" and a.get("data-tab"):
            self.tab_buttons.append(a["data-tab"])
        if a.get("data-box"):
            self.boxes.append(a["data-box"])
        # A trace box is page the reader opens with a click, and the format puts
        # real content there on purpose: a family of notes prints one line and
        # carries every member in its own trace. So it reads as page, not markup.
        for key in ("data-calc", "data-steps", "title"):
            if a.get(key):
                self.trace_parts.append(a[key])
        if tag == "title":
            self._in_title = True
        if tag == "ol" and self._seen_flags_heading and self.flags_text is None \
                and self._flags_buf is None:
            self._flags_buf = []
        if tag == "script":
            if (a.get("type") or "").strip() == "application/json":
                self._script_id = a.get("id")
                self._script_buf = []
            else:
                self._in_script = True
        if tag == "table":
            self._table = {"corner": None, "rows": [],
                           "money": a.get("data-money")}
        if tag == "thead":
            self._in_head_row = True
        if tag == "tr":
            # A statement table stores its trace once per ROW (data-method, plus
            # data-steps where the line is computed) and once per COLUMN header --
            # report-structure's coverage contract, so the template JS composes each
            # cell's answer without a per-cell payload. A cell in such a row answers
            # when clicked, so it counts as traced.
            self._row_traced = bool(a.get("data-method") or a.get("data-steps"))
            self._row = []
        if tag in ("td", "th"):
            self._cell, self._cell_traced, self._cell_span = [], self._row_traced, False
        rec = None
        if cls & TRACEABLE_CLASSES:
            self.traces.append((cls, a.get("data-calc"), a.get("data-hops"),
                                a.get("data-href"), a.get("title")))
            rec = {"classes": cls, "text": [], "in_cell": self._cell is not None,
                   "in_statement": bool(self._table
                                        and self._table.get("statement_section"))}
            self.spans.append(rec)
            if self._cell is not None:
                self._cell_traced = True
                self._cell_span = True
        if tag == "a" and (cls & TRACEABLE_CLASSES):
            self.anchors_in_traceable += 1
        if tag not in VOID:
            self.stack.append((tag, rec))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if tag == "script":
            if self._script_id is not None:
                self.scripts[self._script_id] = "".join(self._script_buf)
                self._script_id, self._script_buf = None, []
            self._in_script = False
        if tag == "title":
            self._in_title = False
        if tag == "ol" and self._flags_buf is not None:
            self.flags_text = re.sub(r"\s+", " ", "".join(self._flags_buf)).strip()
            self._flags_buf = None
        if tag == "tr":
            self._row_traced = False
            if self._table is not None and self._row:
                if self._in_head_row and self._table["corner"] is None:
                    self._table["corner"] = self._row[0]["text"]
                    self._table["statement_section"] = _statement_section(
                        self._table["corner"])
                elif not self._in_head_row:
                    self._table["rows"].append(self._row)
            self._row = None
        if tag == "thead":
            self._in_head_row = False
        if tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None
        if tag in ("td", "th") and self._cell is not None:
            cell = {"text": "".join(self._cell).strip(), "traced": self._cell_traced,
                    "tag": tag, "has_span": self._cell_span,
                    "in_statement": bool(self._table
                                         and self._table.get("statement_section")),
                    "mm": bool(self._table and self._table.get("money") == "mm")}
            self.cells.append(cell)
            if self._row is not None:
                self._row.append(cell)
            self._cell, self._cell_traced, self._cell_span = None, False, False
        # Unwind the tag stack, closing every traced span that ends with it.
        if self.stack and self.stack[-1][0] == tag:
            self._close(self.stack.pop())
        elif any(t == tag for t, _ in self.stack):
            while self.stack:
                top = self.stack.pop()
                self._close(top)
                if top[0] == tag:
                    break
            self.balance.append(f"<{tag}> was closed with tags still open inside it")
        else:
            self.balance.append(f"</{tag}> closes a tag that was never opened")

    @staticmethod
    def _close(entry):
        rec = entry[1]
        if rec is not None:
            rec["text"] = "".join(rec["text"]).strip()

    def handle_data(self, data):
        if self._script_id is not None:
            self._script_buf.append(data)
            return
        if self._in_script or (self.stack and self.stack[-1][0] == "style"):
            if self._in_script:
                self.script_text.append(data)
            return
        if self._in_title:
            self.title += data
        if self._flags_buf is not None:
            self._flags_buf.append(data)
        if self._cell is not None:
            self._cell.append(data)
        for _tag, rec in self.stack:
            if rec is not None:
                rec["text"].append(data)
        self.text_parts.append(data)
        for m in TOKEN.finditer(data):
            self.tokens_left.add(m.group(1))

    @property
    def text(self):
        return re.sub(r"\s+", " ", "".join(self.text_parts))

    @property
    def reader_text(self):
        """Everything the reader can get to: the page, plus every trace box on it."""
        return re.sub(r"\s+", " ", "".join(self.text_parts)
                      + " " + " ".join(self.trace_parts))


def _statement_section(corner):
    """Which statement a table is, read off the corner header the renderer writes."""
    for title, section in STATEMENT_TABLES.items():
        if str(corner or "").strip().startswith(title + " ("):
            return section
    return None


# ------------------------------------------------------------------ helpers
def parse_money(s):
    """A memo cell as a number, or None when it is not one.

    A cell the parser cannot read is a cell the gate never checks, so the forms the
    memo actually writes all have to parse: money in brackets for negative, a
    percentage with an explicit + or -, and a multiple with an x after it. Each form
    missed here was a whole class of figure passing unchecked.
    """
    t = (s or "").strip().replace("—", "").replace("–", "")
    t = t.replace("×", "").replace("%", "").replace(" ", " ")
    # A negative printed with a true minus sign was read as POSITIVE, so every
    # negative figure outside the statement tables was held against its own opposite.
    t = t.replace("−", "-")
    # A figure printed away from a table carries its dollar sign, and a figure the
    # parser cannot read is a figure the gate never checks.
    t = t.replace("$", "").strip()
    mult = 1.0
    if t.rstrip(")").rstrip().endswith("MM"):
        # A move printed in millions -- "+$3.79MM" -- reads back as its dollars.
        i = t.rindex("MM")
        t = (t[:i] + t[i + 2:]).strip()
        mult = 1e6
    if t.endswith(("x", "X")):
        t = t[:-1].strip()
    if not t or not MONEY.match(t):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace(",", "").lstrip("+").strip()
    try:
        v = float(t)
    except ValueError:
        return None
    return (-v if neg else v) * mult


def leading_figure(text):
    """(value, printed form) for a cell that leads with a figure and then says more.

    A covenant cell prints "0.95× At-risk / headroom …" and a year-to-date cell
    prints "1,250 (3 of 6 months carried data)". Reading only the whole string
    leaves both uncompared, which is where a hand-edited covenant value hides.
    """
    m = LEADING_NUMBER.match(str(text or ""))
    if not m:
        return None, ""
    head = m.group(0)
    return parse_money(head), head.strip()


def tolerance_for(printed):
    """A dollar for money; 0.05 for a percentage or a multiple, which print to one
    and two decimals respectively; half a hundredth of a million for a figure
    printed in millions."""
    t = str(printed or "").strip()
    if re.search(r"MM\s*\)?\s*$", t):
        return 5000.0
    return 0.05 if re.search(r"[%xX×]\s*$", t) else 1.0


def payload_figures(payload):
    for sec in (payload.get("sections") or {}).values():
        for f in (sec.get("figures") or []):
            yield f


_ARRAY = r"const\s+%s\s*=\s*\[([^\]]*)\]"
_INLINE = {"REV": "net_revenue", "GP": "gross_profit", "OPEX": "opex", "EB": "ebitda",
           "GPM": "gross_margin", "EBM": "ebitda_margin",
           "OPXM": "opex_pct_of_revenue"}


def inline_series(script):
    """The chart's series read out of inline `const REV =[...]` declarations, in the
    same shape the JSON island carries, or None when the memo has none.

    Exists so the gate judges a hand-assembled memo on the same terms as a rendered
    one. A copied example can retain exactly these declarations.
    """
    months = re.search(_ARRAY % "M", script)
    if not months:
        return None
    out = {"months": [s.strip().strip('"\'') for s in months.group(1).split(",")
                      if s.strip()],
           "series": {}}
    for var, key in _INLINE.items():
        m = re.search(_ARRAY % var, script)
        if not m:
            continue
        vals = []
        for s in m.group(1).split(","):
            s = s.strip()
            if not s:
                continue
            try:
                vals.append(None if s in ("null", "undefined") else float(s))
            except ValueError:
                vals.append(None)
        out["series"][key] = {"values": vals}
    return out if out["series"] else None


def literal_series_vars(script):
    """The chart variables a memo declares as a literal array instead of reading
    them from the run's own data block."""
    found = []
    for var in CHART_VARS:
        m = re.search(_ARRAY % var, script)
        if m and m.group(1).strip():
            found.append(var)
    return found


def _near(a, b, tol=1.0):
    if a is None or b is None:
        return False
    return abs(a - b) <= max(tol, abs(b) * 1e-6)


def _maybe_float(v):
    """A JSON number as a float, or None for anything that is not one -- which
    `_near` then reports as a mismatch rather than raising."""
    try:
        return None if v is None or isinstance(v, bool) else float(v)
    except (TypeError, ValueError):
        return None


def _pretty_date(iso):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(iso or "")):
        return None
    return f"{int(iso[8:10])} {MONTHS[int(iso[5:7]) - 1]} {iso[:4]}"


def _short_date(iso):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(iso or "")):
        return None
    return f"{int(iso[8:10])} {MONTHS[int(iso[5:7]) - 1][:3]} {iso[:4]}"


def _pretty_period(period):
    if not re.fullmatch(r"\d{4}-\d{2}", str(period or "")):
        return None
    return f"{MONTHS[int(period[5:7]) - 1]} {period[:4]}"


# ------------------------------------------------------------------ the numbers
def harvest(payload):
    """Every number the run produced, in the forms the memo may print it in.

    Walked recursively rather than read off a list of keys, because the list of
    keys is what went stale: a figure moved into a new block (the aging buckets,
    the security rows, the stress breakevens) silently stopped being compared. A
    number is collected in its own unit, and a value whose nearest declared unit is
    a percentage is collected out of a hundred as well, because that is how it
    prints.
    """
    bag = set()

    def add(v, unit):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return
        bag.add(round(v))
        bag.add(round(v, 2))
        if str(unit or "").strip().lower() in PCT_UNITS:
            bag.add(round(v * 100.0, 1))

    def walk(node, unit):
        if isinstance(node, dict):
            here = node.get("unit", unit)
            for key, val in node.items():
                if key in ("unit", "period", "test_date", "as_of", "dated",
                           "signed_date", "first_test_date"):
                    continue
                # A key that NAMES itself a percentage is one, whatever unit its
                # parent declares: a ratio covenant's headroom.pct prints +73.5%,
                # and holding it as a ratio left it matching nothing.
                if key == "pct" or key.endswith("_pct"):
                    walk(val, "%")
                else:
                    walk(val, here)
        elif isinstance(node, list):
            for item in node:
                walk(item, unit)
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)):
            add(node, unit)
        elif isinstance(node, str):
            # The run also publishes WORDS that carry figures -- a deal term
            # quoted from the agreement ("3.25% cash interest"), a threshold
            # quote. The page prints those tokens verbatim inside traced cells,
            # so each numeric token a payload string carries is a figure the run
            # produced, in exactly the form it prints.
            for tok in FIGURE_TOKEN.findall(node):
                v = parse_money(tok)
                if v is not None:
                    bag.add(round(v))
                    bag.add(round(v, 2))
                    bag.add(round(v, 3))

    walk(payload.get("sections"), None)
    walk(payload.get("windows"), None)
    return bag


# ------------------------------------------------------------------ the checks
def check_printed(memo, payload, errs, warns):
    """1. Every printed figure matches the payload entry it claims.

    Two tiers. A statement table cell is held to its OWN row and column -- the row
    the label names, the month the column names -- which is the tightest claim the
    page makes. Every other traced figure is held to the run's own numbers: a
    traced figure matching no produced number is invalid.
    """
    checked = check_statement_tables(memo, payload, errs)
    want = harvest(payload)
    orphan = []
    # EVERY figure a container prints, not only the one it leads with: a covenant
    # cell states its value and its headroom in dollars and per cent, and holding
    # the value alone left the two figures beside it uncompared. A container of more
    # than a sentence's length is a term of the deal ("1.75% of aggregate original
    # principal of all Loans…"), whose numbers are quoted words rather than figures
    # this run produced, so it is read whole or not at all.
    def hold(text, mm=False):
        text = str(text or "")
        tokens = ([text] if len(text.split()) > FIGURE_WORDS
                  else FIGURE_TOKEN.findall(text) or [text])
        for tok in tokens:
            v = parse_money(tok)
            if v is None:
                continue
            tol = tolerance_for(tok)
            if re.search(r"MM\s*\)?\s*$", tok.strip()):
                tol = 5000.0        # printed to two decimals of a million
            elif mm and not re.search(r"[%xX×]\s*\)?\s*$", tok.strip()):
                # A bare level in a table whose header says $MM.
                v *= 1e6
                tol = 5000.0
            if not any(_near(v, w, tol) for w in want):
                orphan.append((tok.strip() if tok is not text else text)[:60])

    for span in memo.spans:
        if not span["in_statement"] and not span["in_cell"]:
            hold(span["text"])
    for c in memo.cells:
        if c["traced"] and not c["in_statement"]:
            hold(c["text"], mm=c.get("mm", False))
    if orphan:
        errs.append("a traced figure in the memo matches no figure in the run's own "
                    "results: " + "; ".join(sorted(set(orphan))[:6]))
    if not checked:
        errs.append("the memo carries none of the three statement tables, so the "
                    "figures a reader checks the story against are not on the page")


def check_statement_tables(memo, payload, errs):
    """Each statement cell against its own row and column. Returns how many tables
    were matched, so a memo that lost them all is a failure rather than a pass."""
    matched = 0
    for tbl in memo.tables:
        section = tbl.get("statement_section")
        if not section:
            continue
        matched += 1
        t = ((payload.get("sections") or {}).get(section) or {}).get("table") or {}
        rows = t.get("rows") or []
        months = t.get("months") or []
        scale = float((t.get("display") or {}).get("scale") or 1)
        name = str(tbl["corner"]).split(" (")[0]
        if len(tbl["rows"]) != len(rows):
            errs.append(f"the {name.lower()} shows {len(tbl['rows'])} lines where the "
                        f"run produced {len(rows)} — a line was added or dropped "
                        f"after the figures were gathered")
            continue
        for shown, row in zip(tbl["rows"], rows):
            if not shown:
                continue
            label = shown[0]["text"].strip()
            want_label = str(row.get("label") or "").strip()
            if label != want_label:
                errs.append(f"the {name.lower()} shows '{label[:40]}' where the run "
                            f"produced '{want_label[:40]}'")
                continue
            is_share = str(row.get("unit") or "usd").lower() in PCT_UNITS
            for i, month in enumerate(months):
                if i + 1 >= len(shown):
                    errs.append(f"{name}, {want_label}: the {month} column is missing")
                    break
                _check_cell(name, want_label, month, shown[i + 1]["text"],
                            (row.get("cells") or [None] * len(months))[i]
                            if i < len(row.get("cells") or []) else None,
                            is_share, errs, scale)
            ytd_shown = shown[len(months) + 1] if len(shown) > len(months) + 1 else None
            if ytd_shown is None:
                continue
            if is_share:
                # A share's year to date is that year's own ratio, never a sum: the
                # cell prints the ratio the run worked out, or says why there is
                # none. A figure where the run produced none is a hand edit.
                v, _p = leading_figure(ytd_shown["text"])
                if row.get("ytd") is not None:
                    _check_cell(name, want_label, "the year to date",
                                ytd_shown["text"], row["ytd"], True, errs, scale)
                elif v is not None:
                    errs.append(f"{name}, {want_label}: the year-to-date cell prints "
                                f"{ytd_shown['text'][:24]}, but a share is not added "
                                f"down a year")
                continue
            _check_cell(name, want_label, "the year to date", ytd_shown["text"],
                        row.get("ytd"), False, errs, scale)
    return matched


def _check_cell(table, label, column, shown, value, is_share, errs, scale=1.0):
    """One statement cell against the one payload number it claims."""
    v, printed = leading_figure(shown)
    if value is None:
        if v is not None:
            errs.append(f"{table}, {label}, {column}: the memo prints "
                        f"{printed or shown[:20]} where the run produced no figure")
        return
    if v is None:
        errs.append(f"{table}, {label}, {column}: the memo prints no figure where the "
                    f"run produced one")
        return
    # A statement cell is held EXACTLY, not to the dollar of slack the rest of the
    # memo gets: the gate knows the formatter that wrote it -- whole units of the
    # table's own display scale, or a share to one decimal -- so any slack here is
    # room for an edit to hide in.
    if is_share:
        # The cells of a share row are fractions and print as percentages, so they
        # are compared as percentages -- 0.2998 on the page is 30.0%.
        if abs(v - float(value) * 100.0) >= 0.05:
            errs.append(f"{table}, {label}, {column}: the memo prints {printed} where "
                        f"the run produced {float(value) * 100:.1f}%")
        return
    if round(v) != round(float(value) / scale):
        errs.append(f"{table}, {label}, {column}: the memo prints {printed} where the "
                    f"run produced {round(float(value) / scale):,}")


def check_drawn(memo, payload, errs, warns):
    """2. Every drawn figure matches the payload's series."""
    script = "".join(memo.script_text)
    literals = literal_series_vars(script)
    if literals:
        errs.append("the chart's figures are written into the memo itself rather than "
                    "read from the run (" + ", ".join(literals) + ") — a copied chart "
                    "is how another borrower's revenue reached a memo")
    raw = memo.scripts.get("trend-data")
    if raw is None:
        # A memo assembled by hand declares the chart's series as inline arrays
        # instead of reading them from the run, so the
        # arrays are read out of the script and held to the payload exactly as an
        # island would be -- the check has to work on the shape the defect takes.
        drawn = inline_series(script)
        if drawn is None:
            errs.append("the chart carries no figures this check can read, so there is "
                        "nothing to hold against the run's own numbers")
            return
    else:
        try:
            drawn = json.loads(raw)
        except ValueError as e:
            errs.append(f"the chart's data block is not readable: {e}")
            return
    want = (payload.get("sections") or {}).get("trend_chart") or {}
    wseries = want.get("series") or {}
    dseries = drawn.get("series") or {}
    extra = sorted(set(dseries) - set(wseries))
    if extra:
        errs.append("the chart draws " + ", ".join(extra) + ", which the run did not "
                    "produce — a chart may only draw this borrower's own figures")
    for key in sorted(set(dseries) & set(wseries)):
        dv = (dseries[key] or {}).get("values") or []
        wv = (wseries[key] or {}).get("values") or []
        if len(dv) != len(wv):
            errs.append(f"the chart draws {len(dv)} months of {key} where the run "
                        f"produced {len(wv)}")
            continue
        # The chart rounds money to whole dollars, so a dollar of slack; a margin
        # series is a fraction, so it gets the percentage's slack instead. A chart
        # drawing a different borrower is out by orders of magnitude, never by a
        # rounding step.
        tol = 0.0005 if key.endswith(("margin", "_of_revenue")) else 1.0
        bad = [i for i, (x, y) in enumerate(zip(dv, wv))
               if (x is None) != (y is None)
               or (x is not None and not _near(float(x), float(y), tol))]
        if bad:
            months = want.get("months") or []
            named = ", ".join(str(months[i]) if i < len(months) else str(i)
                              for i in bad[:4])
            errs.append(f"the chart's {key} does not match the run's own figures at "
                        f"{named}")
    if not dseries and wseries:
        errs.append("the chart draws nothing although the run produced series for it")
    check_drawn_quarters(drawn, want, errs)


def check_drawn_quarters(drawn, want, errs):
    """The quarter boxes carry the run's own totals, and only for quarters it totalled.

    A box states a quarter that a reader will quote, so it is held exactly as the
    monthly series are: same figure, same quarter, nothing extra. A memo with no
    boxes is not an error -- a window holding no complete quarter has none to draw.
    """
    wq = {str(q.get("label")): q for q in (want.get("quarters") or [])
          if isinstance(q, dict)}
    dq = {str(q.get("label")): q for q in (drawn.get("quarters") or [])
          if isinstance(q, dict)}
    extra = sorted(set(dq) - set(wq))
    if extra:
        errs.append("the chart calls out " + ", ".join(extra) + ", which the run did "
                    "not total — a quarter box may only state this borrower's own "
                    "figures")
    for label in sorted(set(dq) & set(wq)):
        dvals = (dq[label] or {}).get("values") or {}
        wvals = (wq[label] or {}).get("values") or {}
        over = sorted(set(dvals) - set(wvals))
        if over:
            errs.append(f"the {label} box states {', '.join(over)}, which the run did "
                        f"not total for that quarter")
        for key in sorted(set(dvals) & set(wvals)):
            tol = 0.0005 if key.endswith(("margin", "_of_revenue")) else 1.0
            if not _near(_maybe_float(dvals[key]), _maybe_float(wvals[key]), tol):
                errs.append(f"the {label} box states a {key} that is not the run's "
                            f"own total for that quarter")
        if bool(dq[label].get("spans_basis_break")) != \
                bool(wq[label].get("spans_basis_break")):
            errs.append(f"the {label} box disagrees with the run about whether that "
                        f"quarter spans the reporting-basis change")


def check_identity(memo, payload, memo_path, errs, warns):
    """3. The memo names one borrower and one period, and both are the run's."""
    ident = payload.get("identity") or {}
    borrower = str(ident.get("borrower") or "").strip()
    period = str(ident.get("period") or "")
    text = memo.text
    pretty = _pretty_period(period)
    if borrower and borrower not in text:
        errs.append(f"the memo does not name the borrower this run is for "
                    f"({borrower})")
    if pretty and pretty not in text:
        errs.append(f"the memo does not name the period this run is for ({pretty})")
    # The browser tab is the one label that never shows on the page, so it is the
    # example's datum most often left behind (report-structure, Header).
    words = [w for w in re.split(r"[^A-Za-z0-9]+", borrower) if len(w) >= 4]
    title = memo.title.strip()
    if words and not any(w.lower() in title.lower() for w in words):
        errs.append(f"the browser title reads '{title[:60]}', which does not name this "
                    f"borrower — the tab is the first label the reader sees")
    elif pretty and pretty not in title:
        warns.append(f"the browser title does not name {pretty}")
    base = os.path.basename(memo_path)
    # A borrower is filed under whichever of its names the fund uses, which is often
    # its trading name rather than the first word of its legal one. Any substantial
    # word counts.
    if words and not any(w.lower() in base.lower() for w in words):
        warns.append(f"the file is named {base}, which carries none of the borrower's "
                     f"names — a memo saved beside another deal's is easy to mix up")
    # A second borrower's name must never reach the page.
    others = list(ident.get("other_borrowers") or [])
    # The format example is a worked instance of ANOTHER borrower, and the rule is to
    # replace its data. Its borrower's name surviving into a memo means some of its
    # data survived too -- that is exactly how the example's chart got shipped.
    others += [b for b in EXAMPLE_BORROWERS if b not in borrower]
    for other in others:
        if other and other in text:
            errs.append(f"the memo also names {other}, which is a different borrower")


def check_consistency(payload, memo, errs, warns):
    """4. A component set foots to its stated total, and a figure printed twice
    carries the same value in both places."""
    for label, group in _groups(payload):
        if group.get("foots") is False:
            errs.append(f"{label}: the parts do not add up to the stated total, so a "
                        f"reader adding them gets a different number")
    by_id = {}
    for f in payload_figures(payload):
        key = (f.get("covenant_id") or f.get("metric"), f.get("field"),
               f.get("scope"), f.get("test_date"))
        if key[0] is None:
            continue
        prev = by_id.get(key)
        if prev is not None and not _near(prev.get("value"), f.get("value"), 0.01):
            errs.append(f"{key[0]} is carried twice with different values "
                        f"({prev.get('value')} and {f.get('value')})")
        by_id[key] = f


# Saying the borrower's figures govern is the one prose move that can turn our
# fail into their pass, so it is held to a recorded ruling. The reverse direction
# needs no gate: our own reconstruction governing is the default, and a memo
# repeating the default overstates nothing.
_CERT_GOVERNS = re.compile(
    r"(certified|certificate\w*|borrower'?s?(?: own)?)\s+(?:\w+\s+){0,3}"
    r"(?:basis|figures?|calculation\w*|schedule)\s+(?:\w+\s+){0,2}govern",
    re.I)


def check_governing_basis(payload, memo, errs, warns):
    """9. A memo may not hand a covenant to the borrower's basis.

    The certificate is a cross-check column, not an alternate governing basis.
    This check prevents prose from turning a model failure into a certified pass.
    """
    if not _CERT_GOVERNS.search(memo.text):
        return
    errs.append("the memo says the borrower's certified basis governs. Ours does: "
                "the certificate is the cross-check column beside our reconstruction "
                "(audited > field exam > draft/management financials > compliance "
                "cert). Say what the gap is made of and which component carries it, "
                "and leave the basis alone. Where their figure reads the defined term "
                "better than ours, repoint the model binding at the data layer")


def _groups(payload):
    """Every component set the payload states a total for, wherever it sits.

    The funded-debt group moved out of the exposure box into the capital structure,
    so a check that only read `sections[*].groups` stopped seeing the one group that
    matters. Anything carrying a `foots` verdict is a group.
    """
    found = []

    def walk(node, label):
        if isinstance(node, dict):
            if "foots" in node:
                found.append((node.get("label") or node.get("id") or label, node))
            for key, val in node.items():
                walk(val, node.get("label") or node.get("id") or key)
        elif isinstance(node, list):
            for item in node:
                walk(item, label)

    walk(payload.get("sections"), "a component set")
    return found


def _absent_needles(rec, shared_reasons=frozenset()):
    """What on the page proves this `absent` record reached the reader.

    A record is written for the run, not for the page: the renderer states some of
    them one by one, rolls a family of them into one note that names the subject
    once, and turns others into an "n/e" cell under the covenant's own name and test
    date. So the gate looks for the record's SUBJECT in whichever of those forms the
    page uses, rather than for a sentence the renderer never promised to print.
    """
    what = str(rec.get("what") or "").strip()
    reason = html.unescape(str(rec.get("reason") or "")).strip()
    # (parts, one_showing_each): a needle that belongs to this record alone needs its
    # own showing on the page, so two records cannot both point at one sentence. A
    # FAMILY needle is shared on purpose -- the format rolls a family of records into
    # one note that names the subject once -- so it is not counted.
    needles = []
    m = re.match(r"^(.*?)\s+at\s+(\d{4}-\d{2}-\d{2})$", what)
    if m:
        # A covenant that could not be tested on a date: the page names the covenant
        # and writes the date in words, in the history matrix and the far-out block.
        for form in (_pretty_date(m.group(2)), _short_date(m.group(2))):
            if form:
                needles.append(("its subject and its test date",
                                (m.group(1).strip(), form), True))
    if what:
        needles.append(("its subject", (what,), True))
    # A record's own sentence proves it reached the reader ONLY when that sentence
    # is its own. Two records worded identically -- the same runway note on two burn
    # bases -- would otherwise both point at the one paragraph still on the page,
    # and deleting either would go unnoticed.
    if reason and (not what or reason not in shared_reasons):
        needles.append(("its own words", (reason[:60].strip(),), True))
    if " — " in what:
        # A per-field record ("EBITDA (as-reported) — budget"): the page carries one
        # note for the figure, naming it once for the whole family.
        needles.append(("its subject", (what.split(" — ")[0].strip(),), False))
    return needles


# The kinds of `absent` record this format's renderer has nowhere to put. The run
# records each one and the page shows none of them, so refusing every memo that
# carries one would refuse every memo -- they are REPORTED instead, one note each,
# so the gap stays visible and can be closed in the renderer. Anything NOT on this
# list that reaches no reader is still a refusal, so the list can only go stale in
# the direction that shouts.
# Every gap the run records now has somewhere to land: its own block where the
# block can say it, and the data-quality card for whatever is left over. Nothing
# is excused from reaching a reader.
NOT_YET_SHOWN = set()


def check_omissions(payload, memo, errs, warns):
    """5a. Every `absent` record shows up on the page: the memo shows what it
    could not cover rather than leaving it out.

    A record reaches the reader in whichever form the block it belongs to uses: its
    subject and its test date under an "n/e" cell, its subject on its own, its own
    sentence where the block had nothing to draw, or its family's subject inside the
    one note the run rolled that family into. Nothing else counts, and a trace box
    counts as page -- the format puts real content there on purpose.
    """
    text = memo.reader_text
    unshown = []
    claimed = {}                     # a per-record needle -> how many records claim it
    records = payload.get("absent") or []
    seen = {}
    for rec in records:
        key = html.unescape(str(rec.get("reason") or "")).strip()
        seen[key] = seen.get(key, 0) + 1
    shared = frozenset(k for k, n in seen.items() if n > 1)
    for rec in records:
        needles = _absent_needles(rec, shared)
        if not needles:
            continue
        hit = None
        for _how, parts, own in needles:
            if not all(part and part in text for part in parts):
                continue
            if not own:
                hit = True
                break
            key = parts
            claimed[key] = claimed.get(key, 0) + 1
            # Two records that share one sentence need two showings of it; the second
            # cannot be covered by the first record's paragraph.
            if claimed[key] <= min(text.count(part) for part in parts):
                hit = True
                break
            claimed[key] -= 1
        if hit:
            continue
        what = str(rec.get("what") or "").strip() or str(rec.get("reason") or "")[:40]
        if str(rec.get("kind") or "") in NOT_YET_SHOWN:
            unshown.append(f"'{what}' ({rec.get('kind')})")
            continue
        errs.append(f"the memo does not say that '{what}' could not be covered, so a "
                    f"reader cannot tell it is missing")
    if unshown:
        warns.append(f"the run recorded {len(unshown)} gap(s) this format has no place "
                     "for, so the page does not mention them: " + "; ".join(unshown[:8]))


# Every section the five-tab format promises, and what counts as that section
# being filled. Three kinds:
#   a tuple of payload keys -- the section is filled when one of them carries
#      content, and an `absent` record for the section excuses an empty one;
#   PROSE  -- the section IS a text box the analyst writes; whether it may ship
#      unwritten is the box rule's question (check 7), and for the flags box the
#      run's own open items are checked against it (check 8);
#   ROLLUP -- a list of things that went wrong, which a clean month honestly has
#      none of; the page states that in its own words rather than leaving a hole.
# A section the payload carries and this table does not name is a hard error, so the
# table cannot quietly go stale the way the old nine-section one did.
PROSE = "prose"
ROLLUP = "rollup"
FILLED_BY = {
    "header": ("borrower_legal_name", "one_liner", "our_view"),
    "covenant_strip": ("chips", "figures", "prior_breaches"),
    "snapshot_performance": ("performance_views", "figures"),
    "snapshot_collateral": ("collateral", "chips", "figures"),
    "exec_summary": PROSE,
    "trend_chart": ("series", "months"),
    "deal_company": ("facts", "one_liner"),
    "deal_terms": ("rows", "covenant_summary_row"),
    "deal_transaction": ("elements", "sources"),
    "deal_security": ("rows", "columns"),
    "deal_capstructure": ("rows", "funded_debt"),
    "covenant_detail": ("grid", "history", "far_out", "stress_inputs", "preamble"),
    "income_review": ("table", "figures", "revenue_quality"),
    "cashflow_review": ("table", "figures"),
    "balance_review": ("table", "figures", "liquidity", "aging"),
    "files_registry": ("documents",),
    "data_quality": ROLLUP,
    "flags": PROSE,
}


def check_completeness(payload, errs, warns):
    """5b. A section the format promises either carries content or says why not.

    The other checks hold the page to the payload, so a section the payload never
    filled is invisible to them: the memo prints an empty box, agrees with itself
    perfectly, and passes. That is how a borrower with a pledged-asset map on disk
    got a memo saying it had none. A section with nothing in it and no stated
    reason is refused here.
    """
    sections = payload.get("sections") or {}
    stated = {str(a.get("section") or "") for a in (payload.get("absent") or [])}
    unknown = sorted(set(sections) - set(FILLED_BY))
    for name in unknown:
        errs.append(f"the run produced a '{name.replace('_', ' ')}' section this check "
                    f"does not know how to read, so it cannot tell whether the memo "
                    f"filled it — the list of sections this gate holds is out of date")
    for name, keys in sorted(FILLED_BY.items()):
        if name not in sections:
            errs.append(f"the run produced no '{name.replace('_', ' ')}' section, "
                        f"which every memo in this format carries")
            continue
        if keys in (PROSE, ROLLUP):
            continue
        body = sections.get(name) or {}
        if any(body.get(k) for k in keys):
            continue
        if name in stated:
            continue
        errs.append(
            f"the memo's '{name.replace('_', ' ')}' section carries nothing and the "
            "run does not say why — a reader cannot tell whether it is empty because "
            "there was nothing to report or because something was not read")


def check_coverage(memo, errs, warns):
    """6. Every traceable container answers, nothing is left unfilled, the logo is
    real, and nothing traceable looks like a link."""
    if memo.tokens_left:
        errs.append("the memo still has unfilled places: "
                    + ", ".join(sorted(memo.tokens_left)))
    if memo.tokens_in_attrs:
        errs.append("an unfilled place is hidden inside the markup, where it shows a "
                    "reader nothing and links nowhere: "
                    + ", ".join(sorted(memo.tokens_in_attrs)))
    untraced = [c["text"][:50] for c in memo.cells
                if not c["traced"] and c["tag"] == "td"
                and not DATE_CELL.match(c["text"].strip())
                and not AGING_RANGE_CELL.match(c["text"].strip())
                and not NUMBERED_FILENAME_CELL.match(c["text"].strip())
                and leading_figure(c["text"].split("\n")[0])[0] is not None]
    if untraced:
        errs.append("a number in the memo answers nothing when clicked: "
                    + "; ".join(sorted(set(untraced))[:6]))
    empty = [1 for cls, calc, hops, href, title in memo.traces
             if "trc" in cls and not calc and not hops]
    if empty:
        errs.append(f"{len(empty)} figure(s) carry a trace marker with nothing in it")
    if memo.anchors_in_traceable:
        errs.append(f"{memo.anchors_in_traceable} traceable figure(s) are rendered as "
                    "links — a reader must get the explanation first, the file one "
                    "click deeper")
    if memo.balance:
        errs.extend(memo.balance[:5])


def check_boxes(memo, mode, errs, warns):
    """7. The box rule: what happens when the analyst left a text box unwritten.

    The renderer marks every unwritten box, so the memo carries its own answer to
    "did a person write this". Whether an unwritten box may ship is a fund decision,
    not a code one, so it is one flag and nothing else hangs off it.
    """
    if mode == "allow":
        return
    counted = {}
    for name in memo.boxes:
        counted[name] = counted.get(name, 0) + 1
    lines = []
    for name in sorted(counted):
        if name in KEYED_BOXES:
            lines.append(f"{counted[name]} row(s) in {KEYED_BOXES[name]} carry no "
                         f"written note ({name})")
        else:
            lines.append(f"the {name} box was left unwritten")
    if mode == "refuse":
        errs.extend(lines)
    else:
        warns.extend(lines)


def _flag_needle(item):
    """The one token that identifies an open item, and the forms it may print in.

    Sentence matching would pass a memo that mentions the subject in passing and
    miss one that names the amount only, so the gate picks the item's most specific
    identifying token instead: the clause of the agreement it turns on, else the
    named party or covenant, else the money it is worth.
    """
    text = str(item.get("text") or "")
    # The clause the item turns on -- "§6.8(b)", "S6.8(b)", "6.7(b)".
    m = re.search(r"[§S]?\s?\d+\.\d+\s?\([a-z]\)", text)
    if m:
        core = re.sub(r"^[§S]\s?", "", m.group(0)).strip()
        return core, (core,)
    # The covenant, metric or document these items are actually about is nearly
    # always an acronym -- FCCR, EBITDA, TTM, A/R, QoE, DACA.
    acronyms = re.findall(r"\b[A-Z][A-Z/&.-]{2,}[A-Z]\b", text)
    if acronyms:
        best = max(acronyms, key=len)
        return best, (best,)
    # A named party or document: the longest run of capitalised words, then the
    # longest capitalised word. An item written as an instruction leads with a verb,
    # which names nothing, so those words are passed over.
    phrases = [p for p in
               re.findall(r"\b(?:[A-Z][A-Za-z0-9&./-]+(?:\s+[A-Z][A-Za-z0-9&./-]+)+)\b",
                          text) if len(p) >= 8]
    if phrases:
        best = max(phrases, key=len)
        return best, (best,)
    caps = [w for w in re.findall(r"\b[A-Z][A-Za-z0-9&./-]{5,}\b", text)
            if w.lower() not in INSTRUCTION_WORDS]
    if caps:
        best = max(caps, key=len)
        return best, (best,)
    amount = item.get("amount_usd")
    if amount:
        n = abs(round(float(amount)))
        forms = [f"{n:,}", f"{n / 1_000_000:.1f}MM", f"{n / 1_000_000:.1f}M",
                 f"{n / 1_000_000:.2f}M", f"{round(n / 1000):,}K"]
        return f"${n:,}", tuple(forms)
    words = re.findall(r"[A-Za-z][A-Za-z-]{6,}", text)
    if words:
        best = max(words, key=len)
        return best, (best,)
    return None, ()


def check_flags(payload, memo, errs, warns):
    """8. The flags box covers the run's open items.

    The run gathers the open items; the analyst writes them up. Nothing else on the
    page carries them, so an item the prose leaves out is an ask that quietly does
    not get made. An unwritten box is the box rule's business, not this check's.
    """
    items = ((payload.get("sections") or {}).get("flags") or {}).get("items") or []
    if not items:
        return
    prose = memo.flags_text
    if prose is None:
        errs.append("the memo carries no flags and follow-ups list, so the run's "
                    f"{len(items)} open item(s) reach nobody")
        return
    if "FLAGS" in memo.boxes:
        return
    # Every number printed in the prose, for the amount fallback below.
    prose_amounts = []
    for t in re.findall(r"\d[\d,]*(?:\.\d+)?", prose):
        try:
            prose_amounts.append(float(t.replace(",", "")))
        except ValueError:
            pass
    missing = []
    for item in items:
        token, forms = _flag_needle(item)
        if not forms:
            continue
        if any(form.lower() in prose.lower() for form in forms):
            continue
        # An item is also covered when the prose cites its dollar amount — the
        # analyst writes the exact figure ($34,960,655) where the item's record
        # carries a rounded one ($34,960,000), and either names the item.
        amount = item.get("amount_usd")
        if amount:
            target = abs(float(amount))
            if target and any(abs(n - target) / target <= 0.01
                              for n in prose_amounts):
                continue
        missing.append(token)
    if missing:
        errs.append(f"{len(missing)} of the run's {len(items)} open items are not in "
                    "the flags and follow-ups list, so nobody is asked about them: "
                    + "; ".join(missing[:6]))


def check_tabs(memo, errs, warns):
    """9. The five-tab structure is intact and every deep link lands.

    Every fact lives in exactly one place and the Overview deep-links to it, so the
    tabs and the links between them are the format, not decoration: a renamed pane
    breaks a saved `#tab=` link, and a link to an id that is not in the file is a
    dead end the reader meets with no error message.
    """
    ids = [pid for pid, _active in memo.panes]
    if ids != TABS:
        errs.append("the memo's tabs are " + (", ".join(str(i) for i in ids) or "none")
                    + " where the format's five are " + ", ".join(TABS))
    active = [pid for pid, is_active in memo.panes if is_active]
    if active != [TABS[0]]:
        errs.append("the memo opens on " + (", ".join(str(a) for a in active) or "no tab")
                    + " — it opens on the Overview, and on exactly one tab")
    buttons = ["tab-" + b for b in memo.tab_buttons]
    if sorted(buttons) != sorted(ids):
        errs.append("the tab bar's buttons (" + (", ".join(memo.tab_buttons) or "none")
                    + ") do not match the tabs on the page, so a tab cannot be reached")
    dead = sorted({t for t in memo.anchor_targets if t not in memo.ids})
    if dead:
        errs.append("a link in the memo points at something that is not in the file, "
                    "so it goes nowhere: #" + ", #".join(dead[:6]))


# ------------------------------------------------------------------ entry
def validate(payload_path, memo_path, empty_boxes="warn"):
    """(errors, warnings). Errors refuse the memo; warnings are surfaced."""
    errs, warns = [], []
    with open(payload_path, encoding="utf-8") as fh:
        payload = json.load(fh)
    with open(memo_path, encoding="utf-8") as fh:
        raw = fh.read()
    memo = Memo()
    memo.raw = raw
    memo.feed(raw)
    if memo.stack:
        errs.append("the memo's markup does not close: still open at the end — "
                    + ", ".join(t for t, _ in memo.stack[:5]))
    check_printed(memo, payload, errs, warns)
    check_drawn(memo, payload, errs, warns)
    check_identity(memo, payload, memo_path, errs, warns)
    check_consistency(payload, memo, errs, warns)
    check_omissions(payload, memo, errs, warns)
    check_completeness(payload, errs, warns)
    check_coverage(memo, errs, warns)
    check_boxes(memo, empty_boxes, errs, warns)
    check_flags(payload, memo, errs, warns)
    check_governing_basis(payload, memo, errs, warns)
    check_tabs(memo, errs, warns)
    return errs, warns


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--payload", required=True)
    ap.add_argument("--memo", required=True)
    ap.add_argument("--empty-boxes", choices=("allow", "warn", "refuse"),
                    default="warn",
                    help="how to treat a text box the analyst left unwritten: pass it "
                         "in silence, note it, or refuse the memo")
    a = ap.parse_args()
    try:
        errs, warns = validate(a.payload, a.memo, a.empty_boxes)
    except (OSError, ValueError) as e:
        print(f"could not read an input: {e}", file=sys.stderr)
        return 1
    for w in warns:
        print(f"  note: {w}")
    if errs:
        print(f"REFUSED — {len(errs)} problem(s) in {os.path.basename(a.memo)}:")
        for e in errs:
            print("  -", e)
        print("\nThe memo was not accepted, so the last good one is still in place. "
              "Fix the run that produced it and render again — never edit the memo by "
              "hand, because the next run would lose the edit.")
        return 1
    print(f"OK — every figure in {os.path.basename(a.memo)} matches the run behind it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
