#!/usr/bin/env python3
"""Compose the monitoring memo's HTML from the payload, the template and the prose.

WHY THIS EXISTS
Hand-assembled memo HTML can retain worked-example data or silently drop content
when markup drifts. Rendering from a declared template and payload prevents both.

So the split is: `memo-template.html` owns the FORMAT (its CSS and chart code
survive from the example byte for byte), `memo-payload.json` owns every FIGURE, and
`prose.json` owns the JUDGMENT and nothing else. This file joins them and owns the
markup, so there is no second copy of the class names to diverge from.

FAIL-CLOSED RENDERING
A token the template declares and this file has no producer for is a hard error
naming the token. A token whose payload block is missing does not render empty --
it renders the memo's own "could not cover" wording, taken from the payload's
`absent` record. A declared column the payload cannot fill renders with that reason
rather than being dropped.

THE PROSE SEAM
`prose.json`'s keys are the template manifest's `prose` tokens plus the three KEYED
boxes, so the agent cannot introduce a section, and it carries no figures: a number
in a table is a payload token, so prose and table cannot disagree. A key the
manifest does not declare -- or an id inside a keyed box that this run does not
have -- is an error naming it. `--prose-keys` prints the empty file to fill, with
each keyed box pre-seeded with the ids THIS run expects.

RENDER-TIME FACTS
The run date and plugin version are CLI flags, never payload keys: the payload
stays timestamp-free so the same period re-renders byte-identically.

Usage:
  python render_memo.py --payload <memo-payload.json> --prose <prose.json> \
                        --run-date YYYY-MM-DD --out <memo.html> \
                        [--plugin-version <v>] \
                        [--template <memo-template.html>] \
                        [--empty-boxes allow|refuse]
  python render_memo.py --prose-keys [--payload <memo-payload.json>]
"""
import argparse
import html
import json
import os
import re
import sys
from urllib.parse import quote

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TEMPLATE = os.path.join(_HERE, "memo-template.html")
# plugins/<plugin>/skills/render-memo/references/ -> plugins/<plugin>/
_PLUGIN_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
PLUGIN_JSON = os.path.join(_PLUGIN_ROOT, ".claude-plugin", "plugin.json")

TOKEN = re.compile(r"\{\{([A-Z_0-9]+)\}\}")

# ---------------------------------------------------------------- the prose seam
# The boxes the agent fills, and the three KEYED boxes that have no token of
# their own -- the renderer hands each of those to the producer that builds its
# table, which looks a row's sentence up by that row's payload id.
PROSE_TOKENS = [
    "COLLATERAL_READ", "SUMMARY_UPDATE", "SUMMARY_LIQUIDITY",
    "SUMMARY_COLLATERAL", "SUMMARY_COVENANTS", "SUMMARY_OUTLOOK",
    "DEAL_COMPANY_PROFILE", "DEAL_TRANSACTION", "DEAL_SECURITY_SOURCING",
    "DEAL_LEVERAGE_CONTEXT", "COVENANT_OVERALL", "FORWARD_STRESS",
    "IS_OBSERVATIONS", "REVENUE_QUALITY_NOTES", "CF_OBSERVATIONS",
    "BS_OBSERVATIONS", "LIQUIDITY_READ", "AGING_READ",
    "COLLATERAL_COVERAGE_CLOSING", "DATA_QUALITY_NOTES", "OPEN_QUESTIONS",
    "FLAGS",
]
# The executive summary's five blocks, in the order the card prints them. Each is
# a short prose paragraph sitting after a bolded label the TEMPLATE owns -- the
# writer never writes the label, so the cadence (monthly vs quarterly) cannot
# drift from the period that decides it.
SUMMARY_BOXES = [
    "SUMMARY_UPDATE", "SUMMARY_LIQUIDITY", "SUMMARY_COLLATERAL",
    "SUMMARY_COVENANTS", "SUMMARY_OUTLOOK",
]
# A summary block is a read of figures, so a written one with no figure in it is
# placeholder prose; the outlook and collateral blocks may legitimately carry a
# month with nothing to count, so only these three are held to it.
SUMMARY_NEED_FIGURE = {"SUMMARY_UPDATE", "SUMMARY_LIQUIDITY", "SUMMARY_COVENANTS"}
KEYED_PROSE = ["COVENANT_DELTAS", "CAPSTRUCTURE_NOTES", "DOCUMENT_NOTES"]
ASSET_TOKENS = []

# Where each box sits in the template, which decides what an EMPTY one renders:
# a slot inside a paragraph takes a span (a <p> there would break the sentence in
# two), a slot inside a list takes a list item, everything else takes a paragraph.
BOX_CONTEXT = {
    "COLLATERAL_READ": "inline",
    "SUMMARY_UPDATE": "inline", "SUMMARY_LIQUIDITY": "inline",
    "SUMMARY_COLLATERAL": "inline", "SUMMARY_COVENANTS": "inline",
    "SUMMARY_OUTLOOK": "inline",
    "DEAL_COMPANY_PROFILE": "inline", "DEAL_TRANSACTION": "inline",
    "DEAL_SECURITY_SOURCING": "inline", "DEAL_LEVERAGE_CONTEXT": "inline",
    "COVENANT_OVERALL": "inline", "FORWARD_STRESS": "inline",
    "IS_OBSERVATIONS": "list", "REVENUE_QUALITY_NOTES": "block",
    "CF_OBSERVATIONS": "list", "BS_OBSERVATIONS": "list",
    "LIQUIDITY_READ": "list", "AGING_READ": "block",
    "COLLATERAL_COVERAGE_CLOSING": "inline", "DATA_QUALITY_NOTES": "list",
    "OPEN_QUESTIONS": "block", "FLAGS": "list",
}
# Three boxes render NOTHING when empty rather than a placeholder: two of them
# annotate a block the payload only carries when its module ran, and the third is
# a list of open questions a month may simply not have.
COLLAPSE_EMPTY = {"REVENUE_QUALITY_NOTES": "REVENUE_QUALITY",
                  "AGING_READ": "AGING",
                  "OPEN_QUESTIONS": None}
EMPTY_SENTENCE = "The analyst wrote nothing here for this month."

STATUS_CLASS = {"pass": "pass", "at_risk": "atrisk", "at-risk": "atrisk",
                "breach": "breach", "not_evaluable": "neu", "n/e": "neu"}
STATUS_WORD = {"pass": "Pass", "at_risk": "At-risk", "at-risk": "At-risk",
               "breach": "Breach", "not_evaluable": "Not tested", "n/e": "Not tested"}
# The fixed vocabulary the grid's Result column prints, and the chip it wears.
RESULT_CLASS = {"PASS": "pass", "PASS (at-risk)": "atrisk", "NOT PASSED": "breach"}
RESULT_RANK = {"NOT PASSED": 0, "PASS (at-risk)": 1, "PASS": 2}


class RenderError(Exception):
    """A bad input the operator can act on."""


# ------------------------------------------------------------------ formatting
# A sentence written by one of our own setup steps can carry its own filing note
# into a sentence a credit reader sees -- "...; covenant-spec.json security parse
# 2026-08-04" on the end of a security summary. The clause is true and it is not
# for this reader: the memo cites documents, never the files the run keeps for
# itself. Only the offending clause is dropped, so the document citations beside
# it survive.
# Our own working files: a spec, a map, an engine. The borrower's documents and the
# monitoring workbook are fair to cite, so .pdf and .xlsx are not in here.
_OUR_WORKING_FILE = re.compile(r"[\w.-]+\.(?:json|py|md)\b", re.I)


def reader_prose(text):
    """Prose from a config or map, with any clause naming one of our own files cut."""
    s = str(text or "").strip()
    if not s:
        return s
    kept = [c for c in re.split(r"(?<=[;.])\s+", s) if c.strip()
            and not _OUR_WORKING_FILE.search(c)]
    return " ".join(kept).strip().rstrip(";") or s


def esc(x):
    return html.escape("" if x is None else str(x), quote=True)


def usd(v):
    """Money the way the memo writes it: whole dollars, negatives in brackets.
    A negative that rounds to zero prints (0), keeping the sign the reader
    needs — a flat 0 says the month was even, and it was not."""
    if v is None:
        return "&mdash;"
    n = round(v)
    s = f"{abs(n):,}"
    return f"({s})" if (n < 0 or (n == 0 and v < 0)) else s


def dollars(v):
    """Money that stands on its own, outside a table.

    A table says "US$" once in its corner header, so its cells are bare. A teaser or
    a chip has no header above it, and "17,721,846 funded" leaves the reader to
    assume the unit -- on a borrower whose model is kept in thousands that assumption
    is wrong by a factor of a thousand. So anything printed away from a table carries
    its sign.
    """
    if v is None:
        return "&mdash;"
    s = usd(v)
    return f"({'$'}{s[1:-1]})" if s.startswith("(") else f"${s}"


def pct(v, digits=1):
    """A CHANGE as a percentage: the sign travels with it."""
    if v is None:
        return "&mdash;"
    return f"{'+' if v >= 0 else ''}{v * 100:.{digits}f}%"


def share(v, digits=1):
    """A SHARE as a percentage: 98.0%, never +98.0%."""
    if v is None:
        return "&mdash;"
    return f"{v * 100:.{digits}f}%"


def ratio(v):
    return "&mdash;" if v is None else f"{v:.2f}&times;"


def by_unit(value, unit, sign=True):
    """One value the way its own unit says to print it."""
    u = (unit or "").strip().lower()
    if value is None:
        return "&mdash;"
    if u in ("ratio", "x"):
        return ratio(value)
    if u in ("%", "pct", "percent"):
        return share(value)
    if u == "days":
        return f"{round(value):,} days"
    if u == "months":
        return f"{value:.1f} months"
    return dollars(value) if sign else usd(value)


def fig_text(f, sign=False):
    """A figure as the memo prints it. `sign` puts the dollar sign on money, for a
    column that mixes money with multiples -- a bare 2,000,000 beside a 2.75x reads
    as another multiple."""
    u = (f.get("unit") or "").lower()
    if u in ("ratio", "x"):
        return ratio(f.get("value"))
    if u in ("%", "pct", "percent"):
        return pct(f.get("value"))
    if u == "days":
        return "&mdash;" if f.get("value") is None else f"{round(f['value']):,} days"
    if u == "months":
        return "&mdash;" if f.get("value") is None else f"{f['value']:.1f} months"
    return dollars(f.get("value")) if sign else usd(f.get("value"))


def hops_attr(f):
    """The `data-hops` payload the trace box reads: one entry per hop, each with the
    text a MEMO READER can act on and its link, or a null link when the store did
    not resolve. Never a guessed URL."""
    return esc(json.dumps(hop_list(f.get("hops") or []), sort_keys=True))


# Files that are OURS, not the borrower's: the working records this monitor keeps
# beside the model. A reader of the memo knows the deal's documents and knows
# nothing about these, so a hop naming one answers nothing -- the hop's own
# upstream document is the answer, and where there is none the memo says what the
# hop stands for instead of naming the file.
_INTERNAL_FILE = re.compile(
    r"^(covenant-spec|deal-context|collateral-map|budget-map|source-map|"
    r"restatements|borrower-config|certified-values.*|perf-views.*|"
    r"covenant-compliance.*|budget-vs-actual.*|three-statement-analysis.*|"
    r"ap-ar-aging.*|revenue-quality.*)\.json$", re.I)
_INTERNAL_WORDS = "the deal's own record of this term, kept with the monitoring files"


def hop_list(hops):
    out = []
    for h in hops:
        if not isinstance(h, dict):
            continue
        where = h.get("sheet") or h.get("section")
        name = os.path.basename(str(h.get("path_in_store") or "")) or "the model"
        internal = bool(_INTERNAL_FILE.match(name))
        if internal:
            # The document this record was distilled FROM is the hop a reader can
            # act on, and it is already carried as the upstream entry below.
            if not (h.get("upstream") or []):
                out.append({"t": _INTERNAL_WORDS, "h": None})
        else:
            t = name + (f" — {where}" if where else "")
            if h.get("cells"):
                t += f" ({h['cells']})"
            out.append({"t": t, "h": h.get("url")})
        for u in (h.get("upstream") or []):
            if not isinstance(u, dict):
                continue
            un = os.path.basename(str(u.get("path_in_store") or ""))
            uw = u.get("sheet") or u.get("section")
            out.append({"t": (un or "the borrower's own file") + (f" — {uw}" if uw else ""),
                        "h": u.get("url")})
    return out


# The file kinds SharePoint opens in an Office viewer, and the prefix each one
# answers on. This table and the two URL shapes below are the same rule as
# `store_url` in lib/monitor_lib.py; they are written twice because this
# engine takes no import outside the standard library and reads its url_base
# from the payload rather than from the store catalog.
_OFFICE_VIEWER = {
    ".xlsx": ":x:", ".xlsm": ":x:", ".xlsb": ":x:", ".xls": ":x:", ".csv": ":x:",
    ".docx": ":w:", ".doc": ":w:",
    ".pptx": ":p:", ".ppt": ":p:",
    ".pdf": ":b:",
}


def store_url(store, path_in_store, stores):
    """The link for one ref, joined the ONE way the payload joins it: the envelope's
    own `stores[<store>].url_base` plus the ref's path. A ref whose store is not in
    the envelope gets no link and is named in plain text instead.

    The link OPENS the file. A plain path URL to a document library makes the
    browser fetch the bytes, so a reader tracing a figure to its workbook got the
    file in their Downloads folder rather than the sheet on their screen.
    """
    if not store:
        return None
    entry = (stores or {}).get(store)
    if not isinstance(entry, dict) or not entry.get("url_base"):
        return None
    base = str(entry["url_base"]).rstrip("/")
    tail = [s for s in str(path_in_store or "").replace("\\", "/").split("/") if s]

    # https://host + /sites/<site> + /<library> + /<the rest of the base>
    m = re.match(r"^(https?://[^/]+)(/sites/[^/]+)/([^/]+)/?(.*)$", base)
    if not m or not tail:
        url = base
        for seg in tail:
            url += "/" + quote(seg, safe="")
        return url
    host, site, library, inside = m.groups()
    parts = [p for p in inside.split("/") if p] + tail
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


def relink(node, stores):
    """Rebuild every link the payload carries, from the store and the path beside
    it. Returns the payload.

    Run ONCE on the loaded payload, so no part of the memo can print a link this
    engine did not build. The payload's own links were built by whatever version
    wrote it, and every payload written before this said the file's plain path,
    which SharePoint downloads: a reader tracing a figure to the model got the
    workbook in their Downloads folder rather than the sheet on screen. A link
    whose store the payload does not carry is left as it stands -- it is the one
    shape this cannot rebuild.
    """
    if isinstance(node, list):
        for item in node:
            relink(item, stores)
    elif isinstance(node, dict):
        if "url" in node and node.get("store"):
            built = store_url(node.get("store"), node.get("path_in_store"), stores)
            if built:
                node["url"] = built
                if "linked" in node:
                    node["linked"] = True
        for value in node.values():
            relink(value, stores)
    return node


def refs_to_hops(refs, stores):
    """Raw `source_ref` entries as trace hops, with their links resolved.

    A block the payload carries as refs rather than as figures (the stress
    arithmetic, the aging export) still has to answer when clicked, so its refs are
    resolved here through the envelope's store table -- the same join, so the same
    link the figures already carry.
    """
    hops = []
    for r in (refs or []):
        if not isinstance(r, dict):
            continue
        h = {k: r.get(k) for k in ("store", "path_in_store", "sheet", "row", "label",
                                   "cells", "section", "period") if r.get(k) is not None}
        h["url"] = r.get("url") or store_url(r.get("store"), r.get("path_in_store"),
                                             stores)
        ups = []
        for u in (r.get("upstream") or []):
            if not isinstance(u, dict):
                continue
            uu = {k: u.get(k) for k in ("store", "path_in_store", "sheet", "section")
                  if u.get(k) is not None}
            uu["url"] = u.get("url") or store_url(u.get("store"),
                                                  u.get("path_in_store"), stores)
            ups.append(uu)
        h["upstream"] = ups
        hops.append(h)
    return hops


# ---------------------------------------------------------------- plain trace text
# What an engine writes for its own record and what a memo reader can act on are not
# the same sentence. A module's `calc` note is written in the engine's shorthand --
# `model:sum(...)`, a spec's own key path, a figure in scientific notation -- and
# printed into a trace box it reads as a fault in the tool rather than an answer to
# "where did this number come from". The figures are untouched; only the words are.
_SCI = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)[eE]([+-]?\d+)")
_MODEL_SUM = re.compile(r"model:sum\(([^)]*)\)")
_MODEL_ONE = re.compile(r"model:'([^']*)'|model:([A-Za-z][\w ,&./%()-]*)")
_SPEC_REF = re.compile(r"\s*\((?:threshold )?per covenant-spec [^)]*\)")
_WINDOW = re.compile(r"summed (\w+ \d{4})\.\.(\w+ \d{4}) \((\d+) mo\)")
_TEST = re.compile(r";?\s*test:\s*(max_ratio|min_ratio|min_coverage|min_level|max_level)\s*"
                   r"(-?[\d.,]+)\s*(<=|>=|<|>)\s*(-?[\d.,]+)")
_PRIMITIVE_WORDS = {"max_ratio": "must not exceed", "max_level": "must not exceed",
                    "min_ratio": "must be at least", "min_coverage": "must be at least",
                    "min_level": "must be at least"}


def _plain_number(m):
    v = float(f"{m.group(1)}e{m.group(2)}")
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.4g}"


def _expand_sums(s):
    """`model:sum(a, b)` as "a plus b" -- scanned rather than matched, because a row
    is called "EBITDA (Covenant Bridge)" and a regex stopping at the first bracket
    swallows the row next to it."""
    out, i = [], 0
    while True:
        j = s.find("model:sum(", i)
        if j < 0:
            out.append(s[i:])
            return "".join(out)
        out.append(s[i:j])
        k, depth = j + len("model:sum("), 1
        while k < len(s) and depth:
            depth += (s[k] == "(") - (s[k] == ")")
            k += 1
        out.append(_plain_list(s[j + len("model:sum("):k - 1]))
        i = k


def _plain_list(inner):
    parts, depth, cur = [], 0, []
    for ch in inner:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip()); cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur).strip())
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return parts[0] if parts else inner
    return ", ".join(parts[:-1]) + " plus " + parts[-1]


def plain_calc(text):
    """One engine `calc` note as a sentence a memo reader can read."""
    s = str(text or "")
    if not s:
        return s
    # Figures first: the test clause below reads its two numbers, and it cannot read
    # them while they are still written 2.96262e+06.
    s = _SCI.sub(_plain_number, s)
    s = _SPEC_REF.sub(" (the level the loan agreement sets)", s)
    s = _expand_sums(s)
    s = _MODEL_ONE.sub(lambda m: (m.group(1) or m.group(2) or "").strip(), s)
    s = _WINDOW.sub(lambda m: f"added up over the {m.group(3)} months to {m.group(2)}", s)
    s = _TEST.sub(lambda m: f". The test: {m.group(2)} "
                            f"{_PRIMITIVE_WORDS.get(m.group(1), 'against')} {m.group(4)}", s)
    s = re.sub(r"\[([^\[\]]*)\]\s*/\s*\[([^\[\]]*)\]",
               lambda m: f"{m.group(1).strip()}, divided by {m.group(2).strip()}", s)
    s = s.replace("actual ", "Our figure ").replace("  ", " ")
    return s.strip()


def mth(text):
    """A trace attribute: the engine's note, said plainly, escaped for HTML."""
    return esc(plain_calc(text))


def trc(f, text=None, sign=False):
    """A computed figure's invisible trace span. Never an anchor: a host page
    restyles and navigates real links, and nothing traceable in this memo is
    allowed to look like one."""
    return (f'<span class="trc" data-calc="{mth(f.get("calc"))}" '
            f'data-hops=\'{hops_attr(f)}\'>{text or fig_text(f, sign)}</span>')


def trc_raw(text, calc, hops=None, steps=None):
    """A traced figure the payload carries as a block rather than as a figure leaf."""
    attrs = [f'data-calc="{mth(calc)}"']
    if steps:
        attrs.append("data-steps='" + esc(json.dumps(steps, sort_keys=False)) + "'")
    attrs.append("data-hops='" + esc(json.dumps(hops or [], sort_keys=True)) + "'")
    return f'<span class="trc" {" ".join(attrs)}>{text}</span>'


def srclink(ref, text, stores, detail=None):
    """A DIRECT-SOURCE figure or citation: the file it came from, and its link.

    Nothing here looks like a hyperlink -- the span carries the link and the
    container is the control -- and a ref whose store the envelope does not carry
    renders as `srcmiss`: named, no link, still answering when clicked.
    """
    if not isinstance(ref, dict):
        return text
    url = ref.get("url") or store_url(ref.get("store"), ref.get("path_in_store"),
                                      stores)
    bits = []
    for key in ("section", "sheet", "page", "cells"):
        if ref.get(key) is not None:
            bits.append(f"{key if key != 'section' else 'section'} {ref[key]}"
                        if key in ("section", "page") else str(ref[key]))
    if detail:
        bits.append(detail)
    quote_text = str(ref.get("quote") or "").strip()
    if quote_text:
        bits.append(quote_text if len(quote_text) <= 220 else quote_text[:217] + "…")
    title = str(ref.get("name") or "the source document")
    if bits:
        title += " — " + ", ".join(bits)
    cls = "srclink" if url else "srcmiss"
    href = f' data-href="{esc(url)}"' if url else ""
    return f'<span class="{cls}"{href} title="{esc(title)}">{text}</span>'


def absent_note(rec):
    """One `absent` record as the memo's own wording. This is what a block with no
    payload entry renders as -- never an empty box."""
    what = esc(rec.get("what") or "This")
    return (f'<p class="small muted"><b>{what} is not shown.</b> '
            f'{esc(rec.get("reason"))}</p>')


def absent_for(payload, section, kinds=None):
    return [a for a in payload.get("absent") or []
            if a.get("section") == section and (kinds is None or a.get("kind") in kinds)]


MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
MONTH_ABBR = [m[:3] for m in MONTHS]


def _pretty_date(iso):
    """A test date the way a memo says it: 30 April 2026, not 2026-04-30."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(iso or "")):
        return iso
    return f"{int(iso[8:10])} {MONTHS[int(iso[5:7]) - 1]} {iso[:4]}"


def _short_date(iso):
    """A test date narrow enough for a column header: 30 Apr 2026."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(iso or "")):
        return iso
    return f"{int(iso[8:10])} {MONTHS[int(iso[5:7]) - 1][:3]} {iso[:4]}"


def _pretty_period(period):
    if not period or not re.fullmatch(r"\d{4}-\d{2}", str(period)):
        return period
    return f"{MONTHS[int(period[5:7]) - 1]} {period[:4]}"


def _pretty_when(v):
    """A month or a day the way a memo says it: May 2026, 8 May 2029."""
    s = str(v or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return _pretty_date(s)
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return f"{MONTHS[int(s[5:7]) - 1]} {s[:4]}"
    return v


def _short(month_label):
    a, y = str(month_label).split()
    return f"{a} {y[-2:]}"


def _month_key(label):
    """"Apr 2025" as something sortable, so a list of months reads in order."""
    parts = str(label or "").split()
    if len(parts) == 2 and parts[0][:3] in MONTH_ABBR and parts[1].isdigit():
        return (int(parts[1]), MONTH_ABBR.index(parts[0][:3]) + 1)
    return (9999, 99)


def _span_words(months):
    """A list of month labels as "Feb–Jun 2026" / "Nov 2025 – Jun 2026"."""
    if not months:
        return ""
    if len(months) == 1:
        return str(months[0])
    a, b = str(months[0]), str(months[-1])
    if a.split()[-1] == b.split()[-1]:
        return f"{a.split()[0]}&ndash;{b}"
    return f"{a} &ndash; {b}"


def figures_by_id(payload):
    out = {}
    for key in sorted(payload.get("sections") or {}):
        for f in (payload["sections"][key].get("figures") or []):
            if f.get("id"):
                out.setdefault(f["id"], f)
    return out


# ============================================================ header producers
def render_title(payload):
    name = payload["sections"]["header"].get("borrower_legal_name") \
        or payload["identity"].get("borrower")
    return (f'{esc(name)} &mdash; Monitoring Report &mdash; '
            f'{esc(_pretty_period(payload["identity"].get("period")))}')


def render_h1(payload):
    name = payload["sections"]["header"].get("borrower_legal_name") \
        or payload["identity"].get("borrower")
    return f'{esc(name)} &mdash; Monthly Monitoring Report'


def render_sub_line(payload, run_date, version):
    """Period, fund and the prepared date — and nothing after the date.

    The run date and version are render-time facts, so they arrive as flags: the
    payload stays free of them and the same period re-renders to the same bytes.
    The version rides in an HTML comment beside the line.
    """
    period = _pretty_period(payload["identity"].get("period"))
    fund = payload["identity"].get("fund")
    made = f"credit-monitoring {version}".replace("--", "- -")           # "--" ends an HTML comment early
    fund_text = f' &bull; Fund: {esc(fund)}' if fund else ''
    return (f'Period: <b>{esc(period)}</b> (latest reporting month)'
            f'{fund_text} &bull; Prepared {esc(run_date)}<!-- {made} -->')


def render_deal_team(payload):
    """The deal team, or nothing at all -- never an empty label."""
    team = [str(n).strip() for n in (payload["sections"]["header"].get("deal_team") or [])
            if str(n).strip()]
    if not team:
        return ""
    said = team[0] if len(team) == 1 else ", ".join(team[:-1]) + " and " + team[-1]
    return f'      <p class="sub" style="margin:6px 0 0">Deal team: {esc(said)}</p>'


TRAJECTORY_WORDS = {"improving": "Improving", "stable": "Stable",
                    "deteriorating": "Deteriorating", "worsening": "Deteriorating"}


def render_pills(payload):
    """The header pills: the house view at a glance.

    The watchlist status with its trajectory — read from deal-context's our_view,
    whose calls are the analyst's own. The banner states the view; the Executive
    summary carries the reasoning. A field the deal context does not carry renders
    no pill rather than a guess.
    """
    view = payload["sections"]["header"].get("our_view") or {}
    pills = []
    watch = str(view.get("watchlist") or "").strip().lower()
    traj = TRAJECTORY_WORDS.get(str(view.get("trajectory") or "").lower())
    said = f' &middot; {esc(traj)}' if traj else ""
    if watch in ("on", "yes", "true"):
        pills.append(f'<span class="pill watch">On watchlist{said}</span>')
    elif watch in ("off", "no", "false"):
        pills.append(f'<span class="pill view">Not on watch{said}</span>')
    elif traj:
        pills.append(f'<span class="pill view">Trajectory{said}</span>')
    return "".join(pills)


# June reads as the quarter's story, not June's alone: the cadence belongs to the
# calendar, so the renderer sets it and the same period always renders the same way.
QUARTER_END_MONTHS = {3, 6, 9, 12}


def render_update_label(payload):
    """The first summary block's label: monthly, or quarterly at a quarter close."""
    m = re.fullmatch(r"(\d{4})-(\d{2})",
                     str(payload["identity"].get("period") or ""))
    if m and int(m.group(2)) in QUARTER_END_MONTHS:
        return "Quarterly update"
    return "Monthly update"


# =========================================================== overview producers
def _strip_headline(chips):
    """What the month says in one clause, before any chip is read.

    Three states worth telling apart: the borrower certified compliance and our own
    figures agree; it certified compliance and our figures do not reproduce it; or
    there is no certificate to compare against.

    The certificate's own verdict is stated HERE. It led the strip as a second
    large chip beside the agreement's governing chip, and on a borrower whose
    covenants all sit in one section the two chips carried the same word about the
    same section — the reader was asked to tell apart two boxes that agreed.

    The verdict is read over the agreement's TESTS, so an either/or group counts
    once: a certified miss on one leg of a group the other leg satisfies is not a
    certified breach, and read leg by leg it printed "certified as breaching"
    directly above the group's own green "Compliant".
    """
    real = [c for c in chips if not c.get("analytical")]
    certified = [c for c in real if c.get("cert_value") is not None]
    if not certified:
        return " · no compliance certificate read for this month"
    satisfied = ("pass", "at_risk")
    tests = {}
    for c in certified:
        key = c.get("either_or_group") or ("covenant", c.get("id"))
        ok = str(c.get("cert_status") or "").lower() in satisfied
        tests[key] = tests.get(key, False) or ok
    cert_ok = all(tests.values())
    said = ("borrower certified compliant" if cert_ok
            else "borrower certified as breaching")
    off = [c for c in real
           if str(c.get("cert_status") or c.get("status") or "").lower()
           != str(c.get("status") or "").lower()]
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    tail = ("" if not off else
            f" · {words.get(len(off), len(off))} test"
            + ("s" if len(off) > 1 else "") + " unreconciled")
    return f" · {said}{tail}"


def _chip_name(name):
    """A covenant's name the way a chip says it: the Minimum/Maximum prefix
    dropped where what remains still names the test, sentence case with the
    acronyms kept. The payload has already taken off the either/or-leg tag and
    the parenthetical (memo_payload.covenant_short_name)."""
    words = str(name or "").split()
    if words and words[0].lower() in ("minimum", "maximum") and len(words) >= 3:
        words = words[1:]
    out = []
    for i, w in enumerate(words):
        if w.isupper() and len(w) > 1:
            out.append(w)
        else:
            out.append(w.capitalize() if i == 0 else w.lower())
    return " ".join(out)


def _chip_word(c):
    """The chip's verdict: one word where certificate and model agree, and both
    words where they do not — "Certified pass · model breach" is the whole story
    a reader needs before clicking through."""
    st = str(c.get("status") or "").lower()
    model_word = STATUS_WORD.get(st, str(c.get("status")))
    cert = str(c.get("cert_status") or "").lower()
    if not cert or cert == st:
        return model_word, STATUS_CLASS.get(st, "neu")
    cert_word = STATUS_WORD.get(cert, cert)
    # Certificate and model disagree on the verdict itself: the chip wears the
    # unresolved amber, because neither side's word has been proven yet.
    return (f"Certified {cert_word.lower()} · model {model_word.lower()}",
            "atrisk")


# An either/or group's verdict in the group's own words: a group is compliant or
# it is not, where a single covenant passes or breaches.
GROUP_WORD = {"pass": "Compliant", "at_risk": "Compliant · at-risk",
              "breach": "Breach", "not_evaluable": "Not tested",
              "indeterminate": "Not settled"}


def render_strip(payload):
    chips = payload["sections"]["covenant_strip"].get("chips") or []
    # The card sits directly under the section's own "Covenant compliance" h2
    # (report-structure.md 1.1); its own heading names the period and verdict
    # instead of repeating that title back at the reader.
    if not chips:
        recs = absent_for(payload, "covenant_strip")
        body = "".join(absent_note(r) for r in recs) or absent_note(
            {"what": "Covenant status", "reason": "no covenant falls due this month."})
        return f'<div class="card cov-strip">{body}</div>'
    # An analytical reading is not a covenant of the loan, so it takes no seat on
    # the front of the memo: a reader counting chips counts the tests the agreement
    # actually has. It is shown in Covenant detail, marked as what it is.
    chips = [c for c in chips if not c.get("analytical")]
    if not chips:
        return ('<div class="card cov-strip">'
                + absent_note({"what": "Covenant status",
                               "reason": "no covenant of the agreement falls due "
                                         "this month."}) + '</div>')
    groups = {}
    for c in chips:
        groups.setdefault(c.get("either_or_group"), []).append(c)
    settled = {str(g.get("id")): g for g
               in (payload["sections"]["covenant_strip"].get("either_or") or [])}

    def chip(c, cls_extra=""):
        word, tone = _chip_word(c)
        return (f'<a class="covchip {tone}{cls_extra}" '
                f'href="#sec-covenant"><span class="cdot"></span>'
                f'{esc(_chip_name(c.get("short_name") or c.get("name")))} '
                f'<b>{word}</b></a>')

    out = ['<div class="card cov-strip">',
           f'  <h3>{esc(_pretty_period(payload["identity"]["period"]))} '
           '<span class="small muted" '
           'style="font-weight:400;text-transform:none;letter-spacing:0">'
           f'{_strip_headline(chips)} · click for detail</span></h3>']
    for gid, legs in sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0] or "")):
        if gid is None:
            continue
        # The engine settles each either/or ONCE, so the strip prints that verdict
        # rather than deriving a second one the detail section could contradict.
        # Where no group row reached the payload, any leg passing carries the group.
        g = settled.get(str(gid)) or {}
        st = str(g.get("status") or "").lower() or (
            "pass" if any(str(c.get("status") or "").lower() == "pass"
                          for c in legs) else "breach")
        # A group is named by its section of the agreement, which is what the reader
        # holds; its id is a key in the spec and means nothing to them, so it is
        # never the fallback -- the payload settles the label (memo_payload
        # .either_or_labels) and an unlabelled group is named for what it is.
        label = esc(str(g.get("label") or "Either/or test"))
        out.append('  <div class="cov-overall-row">')
        out.append(f'    <a class="covchip overall lead {STATUS_CLASS.get(st, "neu")}" '
                   f'href="#sec-covenant"><span class="cdot"></span>{label} either/or '
                   f'<b>{GROUP_WORD.get(st, STATUS_WORD.get(st, "Not tested"))}</b></a>')
        out.append('  </div>')
        out.append('  <div class="cov-legs">')
        out += ["    " + chip(c) for c in legs]
        out.append('  </div>')
    # A covenant tested on its own stands beside the group, not under it: it is a
    # covenant of the agreement in its own right, its verdict governs as much as the
    # group's, and a smaller or plainer chip read as a lesser test. So it wears the
    # same `overall lead` chip. Only the legs INSIDE a group render smaller.
    rest = groups.get(None) or []
    if rest:
        out.append('  <div class="cov-rest">')
        out += ["    " + chip(c, " overall lead") for c in rest]
        out.append('  </div>')
    out.append('</div>')
    return "\n".join(out)


def _performance_teaser(payload):
    """Revenue year to date against the same months a year earlier.

    The manifest asks for the prior-year comparison, and it is the one that says
    whether the business is growing. Against-plan is a different question and it
    lives in the box's own `vs Budget` column.
    """
    figs = payload["sections"]["snapshot_performance"].get("figures") or []
    for f in figs:
        if f.get("id") == "performance.revenue-ytd-vs-prior-year" \
                and f.get("value") is not None:
            return f'Net revenue YTD {pct(f["value"])} vs prior year'
    for f in figs:
        if f.get("metric") == "Revenue" and f.get("scope") == "YTD" \
                and f.get("field") == "var_pct":
            return f'Net revenue YTD {pct(f.get("value"))} vs plan'
    return "&mdash;"


def _collateral_teaser(payload):
    """The widest coverage cut the map defines, and the debt it is measured against.

    A bare "0.89x cover" reads as cover over the whole loan. Here it is cover over
    the slice of debt this lien stands behind, which is a different and smaller
    number than funded debt -- so the teaser says which debt, and the reader who
    holds a 1.10x figure from the credit file can see why the two differ.
    """
    col = payload["sections"]["snapshot_collateral"].get("collateral")
    if not col:
        return "&mdash;"
    cuts = [c for c in (col.get("coverage_cuts") or [])
            if c.get("coverage") is not None]
    if not cuts:
        lien = col.get("lien_type")
        return f'{esc(str(lien).capitalize())} lien' if lien else "&mdash;"
    best = max(cuts, key=lambda c: c["coverage"])
    if col.get("secured_debt") is None:
        return f'{esc(best.get("name"))} {ratio(best["coverage"])} cover'
    return (f'Pledged components cover {ratio(best["coverage"])} '
            f'{esc(_secured_word(col))}')


def _secured_word(col):
    """What the cover is measured against, in two or three words."""
    basis = str(col.get("secured_basis") or "").lower()
    if re.search(r"funded debt|line of credit|long-term debt|current maturities",
                 basis):
        return "funded debt"
    if "exposure" in basis:
        return "our exposure"
    return "the debt this lien secures"


def _mm(v):
    """A level in millions, bare: the column header carries the $MM."""
    if v is None:
        return "&mdash;"
    return f"${v / 1e6:,.2f}"


def _mm_move(delta, rel, calc=None, hops=None):
    """A move as "+$3.79MM (+132.3%)", toned by its sign — flat when it prints
    as zero at this precision. Traced when the caller has a source to give it."""
    if delta is None:
        return '<td class="n small muted">&mdash;</td>'
    sign = "+" if delta >= 0 else "&minus;"
    tone = "neu" if round(abs(delta) / 1e6, 2) == 0 else \
        ("up" if delta > 0 else "down")
    body = f"{sign}${abs(delta) / 1e6:,.2f}MM"
    if rel is not None:
        body += f' <span class="cmp">({pct(rel)})</span>'
    if calc:
        body = trc_raw(body, calc, hops or [])
    return f'<td class="n {tone}">{body}</td>'


def _plan_cell(row, stores):
    """A pledged line against the pinned budget's plan for the same month.

    Reads like the MoM and YoY cells beside it — the dollar gap with the percentage
    alongside — and traces to the BUDGET workbook's own cells, because that is
    where the plan came from and not the model row on the same line. An empty cell
    is a plan the budget does not carry, and Data quality says which case it is.
    """
    return _mm_move(row.get("plan_abs"), row.get("plan_pct"),
                    row.get("plan_calc"),
                    hop_list(refs_to_hops(row.get("plan_ref") or [], stores)))


def render_collateral(payload):
    """What secures the loan: the cover it gives, and what is pledged behind it.

    The coverage cuts lead as stat chips, because the ratio is what a reader takes
    away; the components sit under them with the month's and the year's move in
    dollars, with the percentage beside it -- collateral is recovery arithmetic, and
    dollar moves add up across components while percentages do not.
    """
    notes = "".join(absent_note(r) for r in absent_for(payload, "snapshot_collateral"))
    col = payload["sections"]["snapshot_collateral"].get("collateral")
    stores = payload.get("stores") or {}
    if not col:
        return notes or absent_note(
            {"what": "Collateral metrics",
             "reason": "the deal record does not say what secures the loan."})
    chips = []
    for c in (col.get("coverage_cuts") or []):
        if c.get("coverage") is None:
            continue
        cls = " bad" if c["coverage"] < 1.0 else (" warn" if c["coverage"] < 1.25 else "")
        hops = hop_list(refs_to_hops(col.get("secured_ref") or [], stores))
        chips.append(
            f'    <div class="cc{cls}">'
            f'<div class="v">{trc_raw(ratio(c["coverage"]), c.get("calc"), hops)}</div>'
            f'<div class="k">{esc(c.get("name"))}</div></div>')
    chipbox = ("" if not chips else
               '  <div class="covcov">\n' + "\n".join(chips) + '\n  </div>\n')

    period = _pretty_period(col.get("period"))
    mon = f'{str(period).split()[0][:3]} {str(period).split()[-1]}' \
        if period and len(str(period).split()) == 2 else str(period or "")
    heads = [
        ("Pledged component", None),
        (f"{mon} $MM", f"Period-end level read from the model balance sheet "
                       f"for {period}."),
        ("MoM", f"{period} against the prior month's period-end levels — the "
                "dollar move with the percentage alongside, because dollar "
                "deltas add up across components and a percentage on a small "
                "base reads as noise."),
        ("YoY", f"{period} against the same month a year earlier."),
        ("vs Budget", "Each component against the pinned budget's balance-sheet "
                      "plan for the same month, where that plan exists; a "
                      "budget without one leaves this column blank."),
    ]
    hdr = "".join(
        f'<th class="n{" spot" if h == f"{mon} $MM" else ""}">'
        + (esc(h) if calc is None else
           f'<span class="trc" data-calc="{esc(calc)}" data-hops=\'[]\'>{esc(h)}</span>')
        + '</th>'
        if h != "Pledged component" else f'<th>{esc(h)}</th>'
        for h, calc in heads)

    rows = []
    for c in (col.get("pledged_components") or []):
        if c.get("value") is None:
            continue
        hops = hop_list(refs_to_hops(c.get("source_ref") or [], stores))
        # Each component IS a balance-sheet row, and the levels live there: the
        # label links to the statement rather than the memo repeating it.
        label = (f'<a class="jumpname" href="#sec-bs">{esc(c.get("label"))} '
                 f'<span class="arw">&#8599;</span></a>')
        rows.append(
            f'<tr data-method="{mth(c.get("calc"))}">'
            f'<td>{label}</td>'
            f'<td class="n spot">{trc_raw(_mm(c.get("value")), c.get("calc"), hops)}</td>'
            + _mm_move(c.get("mom_abs"), c.get("mom"))
            + _mm_move(c.get("yoy_abs"), c.get("yoy"))
            + _plan_cell(c, stores) + '</tr>')
    total = col.get("pledged_total")
    if total and total.get("value") is not None:
        hops = hop_list(refs_to_hops(total.get("source_ref") or [], stores))
        rows.append(
            f'<tr data-method="{mth(total.get("calc"))}">'
            f'<td><b>{esc(total.get("label"))}</b></td>'
            f'<td class="n spot"><b>{trc_raw(_mm(total.get("value")), total.get("calc"), hops)}</b></td>'
            + _mm_move(total.get("mom_abs"), total.get("mom"))
            + _mm_move(total.get("yoy_abs"), total.get("yoy"))
            + _plan_cell(total, stores) + '</tr>')
    table = ("" if not rows else
             '  <div class="tblwrap">\n  <table class="traced" data-money="mm">\n'
             f'    <thead><tr>{hdr}</tr></thead>\n'
             '    <tbody>\n      '
             + "\n      ".join(rows) + "\n    </tbody>\n  </table>\n  </div>\n")

    # The security package first, in the map's own words, with the debt the cover
    # is measured against bolded inside it: the reader who holds a different
    # coverage figure from the credit file needs the denominator before the chips.
    head = ""
    summary = reader_prose(col.get("security_summary"))
    lead_bits = []
    if summary:
        lead_bits.append(esc(summary if summary.endswith(".") else summary + "."))
    if col.get("secured_debt") is not None:
        lead_bits.append(f'Coverage is measured against '
                         f'<b>{_mm(col["secured_debt"])}MM '
                         f'{esc(_secured_word(col))}</b>.')
    basis = col.get("funded_debt_note")
    if basis:
        lead_bits.append(esc(basis))
    if lead_bits:
        head = ('  <p class="small muted" style="margin:0 0 8px">'
                + " ".join(lead_bits) + '</p>\n')
    return head + chipbox + table + notes


def render_trend_data(payload):
    tc = payload["sections"]["trend_chart"]
    months = tc.get("months") or []
    trend = {"months": [_short(m) for m in months],
             "series": tc.get("series") or {},
             "notes": tc.get("notes") or {},
             "basis_break_index": tc.get("basis_break_index"),
             # The quarter boxes the chart draws over each quarter-end column. The
             # totals are the payload's own, computed and gated before they reach
             # the page; the chart positions them and nothing more.
             "quarters": tc.get("quarters") or []}
    return json.dumps(trend, sort_keys=True)


def render_trend_range(payload):
    months = payload["sections"]["trend_chart"].get("months") or []
    if not months:
        return ""
    return f'({esc(months[0])} &ndash; {esc(months[-1])})'


def _model_ref(payload):
    """The monitoring workbook as a citable document, with the link the figures use.

    The link is READ OFF a figure's own resolved hop rather than built again here,
    so the caption opens exactly the file the traces open.
    """
    path = str(payload["identity"].get("model_path") or "")
    in_store = path.split(":", 1)[1] if ":" in path else path
    name = os.path.basename(in_store.replace("\\", "/"))
    for key in sorted(payload.get("sections") or {}):
        for f in (payload["sections"][key].get("figures") or []):
            for h in (f.get("hops") or []):
                if not isinstance(h, dict):
                    continue
                if os.path.basename(str(h.get("path_in_store") or "")) == name \
                        and h.get("url"):
                    return {"name": name, "url": h["url"],
                            "store": h.get("store"),
                            "path_in_store": h.get("path_in_store")}
    return {"name": name, "url": store_url(path.split(":", 1)[0] if ":" in path else None,
                                          in_store, payload.get("stores") or {}),
            "store": path.split(":", 1)[0] if ":" in path else None,
            "path_in_store": in_store}


def render_trend_caption(payload):
    tc = payload["sections"]["trend_chart"]
    months = tc.get("months") or []
    ref = _model_ref(payload)
    span = f"{months[0]} &ndash; {months[-1]}" if months else ""
    cited = srclink(ref, esc(ref["name"]), payload.get("stores") or {},
                    detail=f"{len(months)} monthly columns")
    quarters = tc.get("quarters") or []
    quarter_line = ""
    if quarters:
        quarter_line = (f' The {len(quarters)} boxes across the top hold complete '
                        f'calendar quarters, added up from the same columns; a '
                        f'quarter the window does not hold in full gets no box.')
    return (f'Chart reads the {len(months)} monthly columns of the monitoring model '
            f'({span}): {cited}. The margins view divides each line by revenue in the '
            f'same month.{quarter_line}')


# =============================================================== deal producers
NOT_ON_FILE_WORDS = "not on file in the deal file"


def render_company_facts(payload):
    company = payload["sections"]["deal_company"]
    facts = company.get("facts") or []
    if not facts:
        return absent_note({"what": "The company facts",
                            "reason": "the deal record carries no company profile."})
    out = []
    for column in ("left", "right"):
        rows = []
        for f in facts:
            if f.get("column") != column:
                continue
            if f.get("present"):
                value = esc(reader_prose(f.get("value")))
                cell = f'<td>{srclink(company.get("source_ref"), value, payload.get("stores") or {})}</td>'
            else:
                cell = f'<td class="muted">{esc(NOT_ON_FILE_WORDS)}</td>'
            rows.append(f'      <tr><td class="muted">{esc(f.get("label"))}</td>'
                        f'{cell}</tr>')
        if rows:
            out.append('    <div><table class="facttable">\n'
                       + "\n".join(rows) + '\n    </table></div>')
    return "\n".join(out)


def _term_method(row):
    """Where a facility term came from, and the note the deal background kept."""
    base = (f'{row["label"]} as the deal background records it, from the executed '
            'loan documents.')
    return f'{base} {row["note"]}' if row.get("note") else base


def _term_value(row):
    v = row.get("value")
    if row.get("kind") == "date":
        return esc(_pretty_when(v))
    return esc(v)


def render_deal_terms(payload, stores):
    sec = payload["sections"]["deal_terms"]
    rows = sec.get("rows") or []
    if not rows:
        return "".join(absent_note(r) for r in absent_for(payload, "deal_terms")) \
            or absent_note({"what": "Key terms",
                            "reason": "the deal record carries no facility terms."})

    def tr(row):
        cell = _term_value(row)
        if not row.get("present"):
            return (f'      <tr><td class="muted">{esc(row.get("label"))}</td>'
                    f'<td class="muted">{cell}</td></tr>')
        # The term is read off the deal background rather than computed, and the
        # trace says so when clicked -- a figure with no answer is one a reader
        # cannot check. The background file itself is never named to the reader.
        traced = trc_raw(cell, _term_method(row), [])
        return (f'      <tr><td class="muted">{esc(row.get("label"))}</td>'
                f'<td>{traced}</td></tr>')

    half = (len(rows) + 1) // 2
    left = [tr(r) for r in rows[:half]]
    right = [tr(r) for r in rows[half:]]
    cov = sec.get("covenant_summary_row") or {}
    if cov:
        value = (esc(cov.get("value")) if cov.get("present")
                 else esc(NOT_ON_FILE_WORDS))
        right.append(
            f'      <tr><td class="muted">{esc(cov.get("label") or "Financial covenants")}'
            f'</td><td>{value} &mdash; the thresholds and this month\'s result are on '
            f'the <a class="jumpname" href="{esc(cov.get("link") or "#sec-covenant")}">'
            f'Covenants tab <span class="arw">&#8599;</span></a></td></tr>')
    return ('    <div><table>\n' + "\n".join(left) + '\n    </table></div>\n'
            '    <div><table>\n' + "\n".join(right) + '\n    </table></div>')


def render_transaction_gaps(payload):
    gaps = payload["sections"]["deal_transaction"].get("gaps") or []
    if not gaps:
        return ""
    words = [str(g.get("words") or g.get("id")) for g in gaps]
    said = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
    return (f'  <div class="notice"><b>Not on file:</b> no document in this deal '
            f'record states {esc(said)}. Those elements are left out of the '
            f'paragraph rather than assumed.</div>')


def render_transaction_sources(payload, stores):
    srcs = payload["sections"]["deal_transaction"].get("sources") or []
    if not srcs:
        return ("The paragraph above rests on the deal record; no executed document "
                "is named for it.")
    cited = []
    for s in srcs:
        label = esc(s.get("name"))
        if s.get("dated"):
            label += f' ({esc(_pretty_when(s["dated"]))})'
        cited.append(srclink(s, label, stores))
    return "Sources: " + ", ".join(cited) + "."


def render_security_asof(payload):
    as_of = payload["sections"]["deal_security"].get("as_of")
    return "" if not as_of else f'&middot; as of {esc(as_of)}'


def render_security_table(payload, stores):
    sec = payload["sections"]["deal_security"]
    cols = sec.get("columns") or []
    rows = sec.get("rows") or []
    if not cols or not rows:
        return "".join(absent_note(r) for r in absent_for(payload, "deal_security")) \
            or absent_note({"what": "The security summary",
                            "reason": "the deal record does not say who holds the loan."})
    # The principal-outstanding column is the one a reader looks for first, so it
    # carries the spot highlight the example gives it.
    spot = next((i for i, c in enumerate(cols) if str(c).startswith("Principal")), None)
    head = "".join(
        f'<th class="{"n spot" if i == spot else ("n" if i else "")}">{esc(c)}</th>'
        if i else f'<th>{esc(c)}</th>' for i, c in enumerate(cols))
    body = []
    for row in rows:
        cells = []
        total = row.get("kind") == "total"
        for i, cell in enumerate(row.get("cells") or []):
            cls = "n spot" if i == spot else ("n" if i else "")
            if cell.get("value") is not None:
                text = by_unit(cell.get("value"), cell.get("unit"))
                if cell.get("source_ref"):
                    text = srclink(cell["source_ref"], text, stores)
            else:
                words = cell.get("absent_words")
                text = ("&mdash;" if words in (None, "—")
                        else f'<span class="small muted">{esc(words)}</span>')
                if cell.get("source_ref") and words not in (None, "—"):
                    text = srclink(cell["source_ref"],
                                   f'<span class="small muted">{esc(words)}</span>',
                                   stores)
            if total and cell.get("value") is not None:
                text = f'<b>{text}</b>'
            if total and i == 0:
                text = f'<b>{text}</b>'
            opens = '<td class="' + cls + '">' if cls else '<td>'
            cells.append(opens + text + '</td>')
        body.append("      <tr>" + "".join(cells) + "</tr>")
    return ('  <table>\n'
            f'    <thead><tr>{head}</tr></thead>\n'
            '    <tbody>\n' + "\n".join(body) + '\n    </tbody>\n  </table>')


def render_capstructure(payload, stores, keyed, empties):
    sec = payload["sections"]["deal_capstructure"]
    rows = sec.get("rows") or []
    if not rows:
        return "".join(absent_note(r) for r in absent_for(payload, "deal_capstructure")) \
            or absent_note({"what": "The capital structure",
                            "reason": "the period-end balance sheet carries no debt "
                                      "balance to deduce it from."})
    period = _pretty_period(payload["identity"].get("period"))
    fd = sec.get("funded_debt") or {}
    body = []
    for row in rows:
        rid = str(row.get("id"))
        hops = hop_list(refs_to_hops([row["source_ref"]] if isinstance(
            row.get("source_ref"), dict) else (row.get("source_ref") or []), stores))
        calc = row.get("ranking") or ""
        if rid == "total_funded_debt" and fd.get("basis"):
            calc = fd["basis"]
        value = by_unit(row.get("value"), row.get("unit") or "usd")
        traced = (trc_raw(value, calc, hops) if row.get("value") is not None
                  else '<span class="small muted">&mdash;</span>')
        strong = rid in ("our_instrument", "total_funded_debt", "total_capitalization")
        if strong and row.get("value") is not None:
            traced = f'<b>{traced}</b>'
        note = keyed_cell(keyed, "CAPSTRUCTURE_NOTES", rid, empties)
        label = esc(row.get("label"))
        if strong:
            label = "<b>" + label + "</b>"
        opens = '      <tr class="sub">' if row.get("sub") else '      <tr>'
        body.append(
            opens
            + f'<td>{label}</td>'
            f'<td class="small muted">{esc(row.get("ranking"))}</td>'
            f'<td class="n spot">{traced}</td>'
            f'<td class="small">{note}</td></tr>')
    parts = ""
    if fd.get("components"):
        n = len(fd["components"])
        skipped = fd.get("empty_rows_excluded") or []
        left = ("" if not skipped else
                " Left out as holding no balance of their own: "
                + ", ".join(str(x) for x in skipped) + ".")
        parts = (f'\n      <tr class="sub"><td colspan="4" class="small muted" '
                 f'data-method="Funded debt adds up '
                 f'{esc(", ".join(str(c) for c in fd["components"]))}.{esc(left)}">'
                 f'Funded debt is made up of {n} debt '
                 f'{"account" if n == 1 else "accounts"} on the balance sheet '
                 f'&mdash; click for the list.</td></tr>')
    return ('  <table class="traced">\n'
            f'    <thead><tr><th>Instrument</th><th>Ranking / security</th>'
            f'<th class="n spot">{esc(period)}</th><th>Pricing / notes</th></tr></thead>\n'
            '    <tbody>\n' + "\n".join(body) + parts + '\n    </tbody>\n  </table>')


# ========================================================== covenant producers
def render_preamble(payload, stores):
    pre = payload["sections"]["covenant_detail"].get("preamble") or {}
    bits = []
    agreement = pre.get("agreement") or {}
    if agreement.get("source_ref") or agreement.get("text"):
        label = "the loan and security agreement"
        if agreement.get("dated"):
            label = f'the loan agreement dated {esc(_pretty_when(agreement["dated"]))}'
        bits.append(srclink(agreement.get("source_ref") or {}, label, stores,
                            detail="the financial-covenant section"))
    for a in (pre.get("amendments") or []):
        name = a.get("name") or a.get("id")
        bits.append(srclink(a.get("source_ref") or {}, esc(name), stores))
    lead = ("" if not bits else ", ".join(bits) + ". ")
    rule = esc(pre.get("rule_plain") or "")
    cert = pre.get("certificate") or {}
    cert_words = ""
    ref = cert.get("source_ref") or {}
    if ref.get("name"):
        who = cert.get("signer")
        when = cert.get("signed_date")
        named = srclink(ref, "the compliance certificate for this month", stores)
        if who and when:
            cert_words = (f' The certified figures come from {named}, signed by '
                          f'{esc(who)} on {esc(_pretty_when(when))}.')
        elif who:
            cert_words = (f' The certified figures come from {named}, signed by '
                          f'{esc(who)}.')
        else:
            cert_words = (f' The certified figures come from {named}; who signed it '
                          f'and on what date was not captured with its figures.')
    return f'{lead}{rule}{cert_words}'


def _headroom_words(h, unit):
    if not isinstance(h, dict):
        return ""
    bits = []
    if h.get("abs") is not None:
        bits.append(by_unit(h["abs"], unit))
    if h.get("pct") is not None:
        bits.append(f'({pct(h["pct"])})')
    return "" if not bits else "headroom " + " ".join(bits)


def render_covenant_grid(payload, stores, keyed, empties):
    sec = payload["sections"]["covenant_detail"]
    grid = sec.get("grid") or []
    figs = figures_by_id(payload)
    pre = sec.get("preamble") or {}
    cert_ref = (pre.get("certificate") or {}).get("source_ref") or {}
    if not grid:
        recs = absent_for(payload, "covenant_detail", {"covenant_no_result"})
        return ("".join(absent_note(r) for r in recs) or absent_note(
            {"what": "The covenant grid",
             "reason": "no financial covenant result was produced for this month."})
            + render_analytical(payload))
    rows = []
    for g in sorted(grid, key=lambda g: (RESULT_RANK.get(g.get("result_word"), 3),
                                         str(g.get("name") or ""))):
        model = g.get("model") or {}
        cert = g.get("certified") or {}
        unit = model.get("unit") or cert.get("unit") or "usd"
        name = (f'{esc(g.get("name"))}'
                + (f'<br><span class="small muted">{esc(g.get("sub_label"))}</span>'
                   if g.get("sub_label") else ""))
        test = (srclink(g.get("threshold_ref") or {}, esc(g.get("test")), stores)
                if g.get("test") else "&mdash;")
        if cert.get("value") is None:
            cert_cell = ('<span class="small muted">no certified figure for this '
                         'covenant</span>')
        else:
            st = str(cert.get("status") or "").lower()
            cert_cell = (srclink(cert_ref, by_unit(cert["value"], unit), stores)
                         + (f' <span class="tag {STATUS_CLASS.get(st, "neu")}">'
                            f'{STATUS_WORD.get(st, esc(cert.get("status")))}</span>'
                            if st else "")
                         + (f'<br><span class="small muted">'
                            f'{_headroom_words(cert.get("headroom"), unit)}</span>'
                            if _headroom_words(cert.get("headroom"), unit) else ""))
        f = figs.get(model.get("figure_id"))
        if model.get("value") is None:
            model_cell = '<span class="small muted">not measured this month</span>'
        elif f is not None:
            model_cell = trc(f, by_unit(model["value"], unit))
        else:
            model_cell = by_unit(model["value"], unit)
        if model.get("value") is not None:
            st = str(model.get("status") or "").lower()
            model_cell += (f' <span class="tag {STATUS_CLASS.get(st, "neu")}">'
                           f'{STATUS_WORD.get(st, esc(model.get("status")))}</span>'
                           if st else "")
            hw = _headroom_words(model.get("headroom"), unit)
            if hw:
                model_cell += f'<br><span class="small muted">{hw}</span>'
        word = g.get("result_word") or "&mdash;"
        delta_text = (keyed.get("COVENANT_DELTAS") or {}).get(str(g.get("id")))
        if not delta_text:
            empties.append(f"COVENANT_DELTAS[{g.get('id')}]")
            delta = '<span class="muted" data-box="COVENANT_DELTAS">&mdash;</span>'
        elif word in ("NOT PASSED", "PASS (at-risk)"):
            # A note on a covenant already in trouble is a material read, not a
            # table aside -- it gets its own visible box, not a legal label.
            delta = f'<div class="notice covnote"><b>Analyst note:</b> {delta_text}</div>'
        else:
            delta = delta_text
        rows.append(
            f'      <tr><td>{name}</td>'
            f'<td class="n">{test}</td>'
            f'<td>{cert_cell}</td>'
            f'<td>{model_cell}</td>'
            f'<td class="small">{delta}</td>'
            f'<td><span class="tag {RESULT_CLASS.get(word, "atrisk")}">{esc(word)}'
            f'</span></td></tr>')
    return ('  <table>\n'
            '    <thead><tr><th>Covenant</th><th class="n">Test</th>'
            '<th>Certified (borrower)</th><th>Model</th>'
            '<th>&Delta; / note</th><th>Result</th></tr></thead>\n'
            '    <tbody>\n' + "\n".join(rows) + '\n    </tbody>\n  </table>'
            + render_analytical(payload))


def render_analytical(payload):
    """A reading the agreement does not contain, kept apart from the covenants.

    An analytical row may re-compute leverage on an adjusted EBITDA basis, so the
    memo can set the agreement basis, the adjusted basis
    and the certified figure side by side. It is the strongest thing the memo can say
    about a certified figure it cannot reproduce -- and it is not a covenant, so it
    sits under the grid saying so, with no status chip and no seat in the history.
    """
    sec = payload["sections"]["covenant_detail"]
    figs = sec.get("figures") or []
    hist = [h for h in (sec.get("history") or []) if h.get("analytical")]
    if not hist:
        return ""
    rows = []
    for h in hist:
        actual = _current(figs, h["id"], "actual")
        req = _current(figs, h["id"], "required")
        if actual is None:
            continue
        name = re.sub(r"^\s*ANALYTICAL\s*[—-]\s*", "", str(h.get("name") or ""))
        rows.append(f'      <tr data-method="{mth(actual.get("calc"))}">'
                    f'<td>{esc(name)}</td>'
                    f'<td class="n">{trc(actual, sign=True)}</td>'
                    f'<td class="n">{trc(req, sign=True) if req else "&mdash;"}</td></tr>')
    if not rows:
        return ""
    return ('\n  <div class="flow readnote"><b>Not a covenant '
            'of the loan.</b> The reading below is our own, shown because it is the '
            'closest basis we can find to the figure the borrower certified. The '
            'agreement sets no such test, so passing it would not be compliance.</div>\n'
            '  <div class="tblwrap">\n  <table class="traced">\n'
            '    <thead><tr><th>Analytical reading</th><th class="n">Our figure</th>'
            '<th class="n">Covenant level it is set against</th></tr></thead>\n'
            '    <tbody>\n' + "\n".join(rows) + '\n    </tbody>\n  </table>\n  </div>')


def _current(figs, cov_id, field):
    for f in figs:
        if f.get("covenant_id") == cov_id and f.get("field") == field and f.get("current"):
            return f
    return None


def render_stress_facts(payload, stores):
    """The traced arithmetic a forward read has to agree with.

    Three facts and no scenario: where the agreement's own schedule goes next, how
    much room the tightest test has today, and what size of move puts each test on
    its threshold. The scenario prose is the analyst's, in the box below.
    """
    si = payload["sections"]["covenant_detail"].get("stress_inputs") or {}
    figs = figures_by_id(payload)
    hist = {h["id"]: h.get("name") or h["id"]
            for h in (payload["sections"]["covenant_detail"].get("history") or [])}
    rows = []
    for s in (si.get("schedule_steps") or []):
        name = hist.get(s.get("covenant"), s.get("covenant"))
        when = _pretty_date(s.get("test_date"))
        text = srclink(s.get("source_ref") or {},
                       by_unit(s.get("required"), s.get("unit")), stores)
        rows.append(f'      <tr><td>{esc(name)} &mdash; the level required at '
                    f'{esc(when)}</td><td class="n">{text}</td></tr>')
    hn = si.get("headroom_now") or {}
    if hn.get("value") is not None:
        f = figs.get(hn.get("figure_id"))
        text = (trc(f, pct(hn["value"])) if f is not None else pct(hn["value"]))
        which = ""
        if f is not None and f.get("label"):
            which = str(f["label"]).split(" — ")[0]
        rows.append(f'      <tr><td>Room on the tightest test today'
                    f'{f" &mdash; {esc(which)}" if which else ""}</td>'
                    f'<td class="n">{text}</td></tr>')
    for b in (si.get("breakevens") or []):
        hops = hop_list(refs_to_hops(b.get("source_ref") or [], stores))
        rows.append(f'      <tr><td>{esc(b.get("label"))}</td>'
                    f'<td class="n">'
                    f'{trc_raw(by_unit(b.get("value"), b.get("unit")), b.get("calc"), hops)}'
                    f'</td></tr>')
    if not rows:
        recs = absent_for(payload, "covenant_detail", {"schedule_absent"})
        return "".join(absent_note(r) for r in recs) or absent_note(
            {"what": "The stress arithmetic",
             "reason": "no covenant result for this month carries a level to measure "
                       "a move against."})
    return ('  <p class="small muted" style="margin:0 0 6px">What a forward read has '
            'to agree with: where the agreement\'s own schedule goes next, the room '
            'the tightest test has today, and the move that lands each test on its '
            'threshold. <b>Click any figure</b> for its arithmetic.</p>\n'
            '  <div class="tblwrap">\n  <table class="traced">\n'
            '    <tbody>\n' + "\n".join(rows) + '\n    </tbody>\n  </table>\n  </div>')


# The matrix runs one column per test date, so a full word in every cell makes it
# unreadable at twenty-four columns. One letter each, coloured, with the legend
# above it and the figures one click inside every cell.
HISTORY_MARK = {"pass": ("P", "g"), "at_risk": ("A", "a"), "at-risk": ("A", "a"),
                "breach": ("B", "rd"), "not_evaluable": ("n/e", "muted"),
                "n/e": ("n/e", "muted")}
# Reasons a test could not be evaluated, in the reader's words rather than the
# engine's own tag.
NOT_EVALUABLE_WORDS = {
    "insufficient_history": "the window this test needs reaches back before the "
                            "workbook's first month, so no figure can be built",
    "missing_inputs": "the workbook has no figure for one of the lines this test "
                      "is built from",
    "no_threshold": "the agreement sets no level for this date",
}


def _plain_reason(reason):
    key = str(reason or "").strip()
    if key in NOT_EVALUABLE_WORDS:
        return NOT_EVALUABLE_WORDS[key]
    return plain_calc(key.replace("_", " ")) if key else ""


def _steps_text(markup):
    """A figure for a trace STEP: the steps table is plain text, not markup."""
    return (str(markup).replace("&times;", "x").replace("&mdash;", "—")
            .replace("&minus;", "-").replace("&amp;", "&"))


def render_covenant_history(payload):
    hist = [h for h in (payload["sections"]["covenant_detail"].get("history") or [])
            if not h.get("analytical")]
    if not hist:
        return ""
    dates = sorted({t["test_date"] for h in hist for t in h["tests"] if t.get("test_date")})
    if not dates:
        return ""
    head = "".join(f'<th class="n">{esc(_short_date(d))}</th>' for d in dates)
    teaser = ""
    body = []
    for h in hist:
        by = {t.get("test_date"): t for t in h["tests"]}
        tally = h.get("tally") or {}
        n = len(h["tests"])
        # The lead states the tally's own arithmetic. A row of six untestable
        # months once read "passed all 6 tests" -- a false statement over its own
        # n/e cells -- because anything short of a breach was called a pass.
        if tally.get("breach"):
            lead = f'breached {tally["breach"]} of {n} tests'
        elif tally.get("pass", 0) == n:
            lead = f'passed all {n} tests'
        elif tally.get("pass", 0):
            lead = (f'passed {tally["pass"]} of {n} tests; the rest could not '
                    f'be measured')
        else:
            lead = f'none of the {n} tests could be measured'
        if tally.get("breach") and not teaser:
            teaser = f'{h.get("name")} — {lead}'
        unit = "ratio" if h.get("primitive") in ("max_ratio", "min_coverage") else "usd"
        cells = []
        for d in dates:
            t = by.get(d)
            if t is None:
                cells.append('<td class="n muted">&mdash;</td>')
                continue
            st = str(t.get("status") or "").lower()
            mark, colour = HISTORY_MARK.get(st, (esc(st), "muted"))
            steps = []
            if t.get("required") is not None:
                steps.append({"l": "Required",
                              "v": _steps_text(by_unit(t["required"], unit))})
            if t.get("actual") is not None:
                steps.append({"l": "Our figure",
                              "v": _steps_text(by_unit(t["actual"], unit))})
            steps.append({"l": "Result",
                          "v": STATUS_WORD.get(st, st), "res": True})
            why = _plain_reason(t.get("reason"))
            calc = (f'{h.get("name")} at {_pretty_date(d)}'
                    + (f' — {why}' if why else '')
                    + ('. The borrower certified this date as well.'
                       if t.get("cert_status") else ''))
            cell = trc_raw(f'<span class="{colour}">{mark}</span>', calc, [], steps)
            cells.append(f'<td class="n">{cell}</td>')
        body.append(f'      <tr><td>{esc(h["name"])} '
                    f'<span class="small muted">({esc(lead)})</span></td>'
                    + "".join(cells) + '</tr>')
    legend = ('  <p class="small muted" style="margin:0 0 8px"><b class="g">P</b> '
              'passed &middot; <b class="a">A</b> passed within 15% of the level '
              '&middot; <b class="rd">B</b> below what the agreement requires '
              '&middot; <b class="muted">n/e</b> could not be evaluated. '
              '<b>Click any cell</b> for that test\'s required level and our '
              'figure.</p>\n')
    return ('\n<details class="card snapbox" id="box-cov-history">\n'
            '  <summary><h3>Covenant history</h3>'
            f'<span class="sbx-teaser">{esc(teaser)}</span>'
            '<span class="chev" aria-hidden="true">&#9656;</span></summary>\n'
            f'  <div class="sbx-body">\n{legend}{_certified_note(hist, dates)}'
            '<div class="tblwrap">\n  <table>\n'
            f'    <thead><tr><th>Covenant</th>{head}</tr></thead>\n'
            '    <tbody>\n' + "\n".join(body) +
            '\n    </tbody>\n  </table>\n  </div></div>\n</details>')


def _certified_note(hist, dates):
    """Which of these verdicts the borrower's own certificate stands behind.

    Every cell in this table reads the same, so a row of Pass / Pass / At-risk /
    Breach looks equally settled all the way across. It is not: the borrower files a
    compliance certificate for the reported month, and the earlier months are our own
    calculation from the model with nothing to check them against. A reader taking a
    breach to the borrower needs to know which it is.
    """
    certified = sorted({t.get("test_date") for h in hist for t in h["tests"]
                        if t.get("cert_status") and t.get("test_date")})
    if not certified:
        return ('  <p class="small muted" style="margin:0 0 8px">Every verdict here '
                'is our own calculation from the model. No compliance certificate '
                'from the borrower was read for any of these dates.</p>\n')
    if len(certified) >= len(dates):
        return ('  <p class="small muted" style="margin:0 0 8px">Each verdict here is '
                'our own calculation, and the borrower filed a compliance '
                'certificate for every one of these dates.</p>\n')
    said = ", ".join(_pretty_date(d) for d in certified)
    return ('  <p class="small muted" style="margin:0 0 8px">A compliance certificate '
            f'from the borrower was read for {esc(said)}. The other dates are our own '
            'calculation from the model, with nothing from the borrower to check them '
            'against.</p>\n')


def render_farout(payload, stores):
    """Covenants whose next test is more than a year out.

    One row each, no stress rows and no strip chip: they are stated so a reader
    knows they exist, and left out of everything that implies they bind this month.
    A borrower with none of them gets the fallback sentence, which is the common case.
    """
    rows = payload["sections"]["covenant_detail"].get("far_out") or []
    if not rows:
        return ('  <p class="small muted" style="margin-bottom:0">None this run '
                '&mdash; every financial covenant in this facility is tested within '
                'the next twelve months, so all of them sit in the grid above.</p>')
    trs = []
    for r in rows:
        threshold = esc(r.get("threshold")) if r.get("threshold") else "&mdash;"
        if r.get("threshold_ref"):
            threshold = srclink(r["threshold_ref"], threshold, stores)
        trs.append(f'      <tr><td>{esc(r.get("name") or r.get("id"))}</td>'
                   f'<td class="n">{esc(_pretty_date(r.get("first_test_date"))) if r.get("first_test_date") else "not scheduled yet"}</td>'
                   f'<td class="n">{threshold}</td></tr>')
    return ('  <div class="tblwrap">\n  <table>\n'
            '    <thead><tr><th>Covenant</th><th class="n">First test</th>'
            '<th class="n">Threshold</th></tr></thead>\n'
            '    <tbody>\n' + "\n".join(trs) + '\n    </tbody>\n  </table>\n  </div>')


# ========================================================= financials producers
def _basis_line(payload, section, kind):
    """What the table above shows, on what basis, in what unit, from where."""
    t = payload["sections"][section].get("table")
    stores = payload.get("stores") or {}
    ref = _model_ref(payload)
    if not t:
        return ""
    months = t.get("months") or []
    ytd = t.get("ytd_months") or []
    bits = [f'Displayed: trailing {len(months)} months ({_span_words(months)})']
    if kind == "balance":
        bits[0] += ", period-end balances"
        bits.append("No year-to-date column &mdash; balances do not sum")
    else:
        if ytd:
            bits[0] += f' plus the year to date ({_span_words(ytd)})'
        bits.append("Analysis basis: the full year to date")
    if kind == "cashflow":
        methods = [str(r.get("method") or "") for r in (t.get("rows") or [])]
        as_reported = sum(1 for m in methods if m.startswith("As the borrower reported"))
        bits.append("The borrower files its own indirect cash-flow statement each "
                    "month and these are its figures as reported"
                    if as_reported * 2 >= max(1, len(methods)) else
                    "This is an indirect statement derived from the balance sheet "
                    "and the income statement")
    scale = _display_scale(t)
    if scale == 1000:
        # A worked example makes the unit stick: the reader sees 6,018 and the
        # sentence says what it is.
        ex = next((r["cells"][-1] for r in (t.get("rows") or [])
                   if (r.get("unit") or "usd") == "usd"
                   and r.get("cells") and r["cells"][-1]), None)
        said = ("" if ex is None else
                f" &mdash; {usd(ex / scale)} means ${abs(ex) / 1e6:.2f}MM")
        bits.append(f"Figures in thousands of dollars{said}")
    elif scale != 1:
        bits.append(f"Figures in US dollars divided by {scale:g}")
    else:
        bits.append("All figures in US dollars")
    cited = srclink(ref, esc(ref["name"]), stores,
                    detail=f'{t.get("sheet")}, {_span_words(months)}')
    return (". ".join(bits) + f'. Source: {cited}. <b>Click any figure</b> for its '
            'calculation and source.')


def _display_scale(t):
    """The unit a statement table PRINTS in — the borrower's own reporting unit.

    The payload's figures are always exact dollars; the page shows them the way
    the finance reader spreads them, in the model's own thousands, and the corner
    header plus the basis line say so. The trace behind each cell still opens on
    the exact dollar figure.
    """
    return float((t.get("display") or {}).get("scale") or 1)


def _unit_word(scale):
    return {1: "(US$)", 1000: "($000)", 1000000: "($MM)"}.get(int(scale),
                                                              f"(US$ / {scale:g})")


def render_table(payload, section, title):
    """One statement's trailing months, with every cell answering when clicked.

    Every figure the payload carries is in dollars; the table prints in the
    borrower's own reporting unit, said in the corner header and the basis line.
    """
    t = payload["sections"][section].get("table")
    if not t:
        recs = absent_for(payload, section, {"table_absent"})
        return "".join(absent_note(r) for r in recs) or absent_note(
            {"what": title, "reason": "the borrower's reporting for these months "
                                      "carries no rows for this statement."})
    months = t["months"]
    scale = _display_scale(t)
    ytd_year = (t.get("ytd_months") or months or ["? ?"])[-1].split()[-1]
    ytd_head = f"YTD {ytd_year}"
    cols = list(months) + ([ytd_head] if t.get("flow") and t.get("ytd_months")
                           else [])

    def money(v):
        return usd(None if v is None else v / scale)

    head = "".join(
        f'<th class="n"><span class="trc" data-calc="{esc(_col_calc(t, c, ytd_head))}" '
        f'data-hops=\'[{{"t":"The borrower&apos;s {esc(t["sheet"].lower())}","h":null}}]\'>'
        f'{esc(_short(c) if c in months else c)}</span></th>' for c in cols)
    body = []
    for r in t["rows"]:
        is_share = (r.get("unit") or "usd") in ("pct", "%", "percent")
        show = share if is_share else money
        cells = []
        for i, mo in enumerate(months):
            cells.append(f'<td class="n">{show(r["cells"][i])}</td>')
        if ytd_head in cols:
            if is_share:
                # A share is not added down a year: January's 30% plus February's
                # 31% is not a margin. When the run worked out the year's own
                # ratio it prints; otherwise the cell says why not.
                if r.get("ytd") is not None:
                    cells.append(f'<td class="n spot">{share(r["ytd"])}</td>')
                else:
                    cells.append('<td class="n muted">&mdash;'
                                 '<span class="cmp">a share is not added up</span>'
                                 '</td>')
            else:
                # A year to date that covers fewer months than it asked for says so:
                # a partial sum must not be presented as a full year.
                cov = ""
                if r.get("ytd_months_covered") is not None and \
                        r["ytd_months_covered"] < (r.get("ytd_months_requested") or 0):
                    cov = (f' <span class="cmp">({r["ytd_months_covered"]} of '
                           f'{r["ytd_months_requested"]} months carried data)</span>')
                cells.append(f'<td class="n spot">{money(r.get("ytd"))}{cov}</td>')
        klass = ' class="sub"' if r.get("sub") else ""
        body.append(f'      <tr{klass} data-method="{mth(r["method"])}">'
                    f'<td>{esc(r["label"])}</td>' + "".join(cells) + '</tr>')
    return ('  <table class="traced">\n'
            f'    <thead><tr><th>{esc(title)} {_unit_word(scale)}</th>{head}</tr>'
            '</thead>\n'
            '    <tbody>\n' + "\n".join(body) + '\n    </tbody>\n  </table>')


def _col_calc(t, col, ytd_head="YTD"):
    if col != ytd_head:
        return f"The borrower's {t['sheet'].lower()} for {col}."
    n = len(t.get("ytd_months") or [])
    return (f"The {n} months of this fiscal year to date, added up. Balances are never "
            f"added this way, so only the flow statements carry this column.")


def render_revenue_quality(payload, stores):
    """The recurring-revenue block, when the module that measures it ran."""
    rq = payload["sections"]["income_review"].get("revenue_quality")
    if not rq:
        return ""
    rows = []
    for leg in (rq.get("waterfall") or []):
        hops = hop_list(refs_to_hops(leg.get("source_ref") or [], stores))
        rows.append(f'      <tr><td>{esc(leg.get("label"))}</td>'
                    f'<td class="n">'
                    f'{trc_raw(by_unit(leg.get("value"), leg.get("unit") or "usd"), leg.get("calc") or leg.get("label"), hops)}'
                    f'</td></tr>')
    table = ("" if not rows else
             '    <div class="tblwrap">\n    <table>\n'
             '      <thead><tr><th>Recurring revenue bridge</th>'
             '<th class="n">US$</th></tr></thead>\n'
             '      <tbody>\n' + "\n".join(rows) + '\n      </tbody>\n    </table>\n'
             '    </div>')
    ret = rq.get("retention") or {}
    bits = []
    if ret.get("gross") is not None:
        bits.append(f'Gross retention <b>{share(ret["gross"])}</b>')
    if ret.get("net") is not None:
        bits.append(f'net retention <b>{share(ret["net"])}</b>')
    basis = f' <span class="small muted">({esc(ret["basis"])})</span>' \
        if ret.get("basis") else ""
    side = ("" if not bits else
            '    <ul class="small" style="margin-top:6px">\n      <li>'
            + ", ".join(bits) + basis + '</li>\n    </ul>')
    return ('  <h3 style="margin-top:12px">Revenue quality</h3>\n'
            '  <div class="grid2">\n' + table + "\n" + side + '\n  </div>')


LIQUIDITY_ORDER = ["cash", "cash-floor", "burn-1m", "burn-3m-avg",
                   "months-to-floor-1m", "months-to-floor-3m"]


def render_liquidity(payload, stores):
    """How long the cash lasts, printed from the run rather than worked out here.

    Both burn bases show side by side because they answer differently and the memo's
    own rule for the liquidity read turns on which of them has a figure. A memo that
    computed its own runway published a third number matching neither archived one
    and traceable to nothing, so this prints the archived figures and each
    one answers when clicked.
    """
    block = payload["sections"]["balance_review"].get("liquidity") or {}
    rows = block.get("rows") or []
    figs = figures_by_id(payload)
    if not rows:
        recs = absent_for(payload, "balance_review", {"liquidity_absent"})
        return "".join(absent_note(r) for r in recs)
    order = {k: i for i, k in enumerate(LIQUIDITY_ORDER)}
    trs = []
    for row in sorted(rows, key=lambda r: (order.get(str(r.get("id")), 99),
                                           str(r.get("id")))):
        f = figs.get(row.get("figure_id"))
        text = by_unit(row.get("value"), row.get("unit"))
        cell = (trc(f, text) if f is not None
                else trc_raw(text, block.get("calc"),
                             hop_list(refs_to_hops(block.get("source_ref") or [],
                                                   stores))))
        trs.append(f'      <tr><td>{esc(row.get("label"))}</td>'
                   f'<td class="n">{cell}</td></tr>')
    gaps = "".join(
        f'  <p class="small muted" style="margin:6px 0 0">'
        f'<b>{esc(a.get("what"))}:</b> {esc(a.get("reason"))}.</p>\n'
        for a in absent_for(payload, "balance_review", {"runway_not_measured"}))
    return ('  <p class="small muted" style="margin:0 0 6px">Two ways of measuring '
            'the burn, because they answer differently. <b>Click any figure</b> to '
            'see how it was worked out.</p>\n'
            '  <div class="tblwrap">\n  <table>\n'
            '    <tbody>\n' + "\n".join(trs) + '\n    </tbody>\n  </table>\n  </div>\n'
            + gaps)


def render_aging(payload, stores):
    """Receivables and payables by age, when the export was delivered."""
    ag = payload["sections"]["balance_review"].get("aging")
    if not ag:
        return ""
    tables = []
    for side, title in (("ar", "Receivables"), ("ap", "Payables")):
        block = ag.get(side) or {}
        hops = hop_list(refs_to_hops(block.get("source_refs") or [], stores))
        calc = block.get("calc") or f"{title} by age, as the borrower exported them."
        buckets = block.get("buckets") or []
        if not buckets and block.get("total") is None:
            continue
        trs = []
        for b in buckets:
            trs.append(f'      <tr><td>{esc(b.get("label"))}</td>'
                       f'<td class="n">{trc_raw(usd(b.get("value")), calc, hops)}</td>'
                       f'<td class="n small">{trc_raw(share(b.get("pct")), calc, hops)}</td></tr>')
        if block.get("total") is not None:
            trs.append(f'      <tr><td><b>Total</b></td>'
                       f'<td class="n"><b>'
                       f'{trc_raw(usd(block["total"]), calc, hops)}'
                       f'</b></td><td class="n small">&mdash;</td></tr>')
        tables.append('    <div class="tblwrap">\n    <table>\n'
                      f'      <thead><tr><th>{title} by age (US$)</th>'
                      '<th class="n">Amount</th><th class="n">Share</th></tr></thead>\n'
                      '      <tbody>\n' + "\n".join(trs)
                      + '\n      </tbody>\n    </table>\n    </div>')
    if not tables:
        return ""
    as_of = ("" if not ag.get("as_of") else
             f' <span class="small muted" style="font-weight:400;text-transform:none;'
             f'letter-spacing:0">&mdash; {esc(ag["as_of"])}</span>')
    return ('  <h3 style="margin-top:12px">A/R and A/P aging' + as_of + '</h3>\n'
            '  <div class="grid2">\n' + "\n".join(tables) + '\n  </div>')


# =============================================================== files producers
FILE_GROUP_WORDS = [
    ("loan_documentation", "Loan documentation"),
    ("underwriting", "Underwriting &amp; diligence"),
    ("monthly_reporting", "Monthly reporting"),
    ("budget", "Budget &amp; forecast"),
    ("working_analysis", "Working analysis artifacts"),
]
ARCHIVE_ROW_ID = "working_analysis.archived_outputs"


def registry_rows(payload):
    """The registry's rows, with this run's archived outputs collapsed into one.

    The archive holds one file per analysis module, and a reader auditing sources
    needs to know they exist -- not to open six machine files. So they become ONE
    row whose location is described in words, exactly as the spec asks, and the
    count says how many there are.
    """
    docs = payload["sections"]["files_registry"].get("documents") or []
    rows, archived = [], []
    for d in docs:
        if d.get("group") == "working_analysis" and d.get("id") != "working_analysis.model":
            archived.append(d)
            continue
        rows.append(d)
    if archived:
        n = len(archived)
        rows.append({"id": ARCHIVE_ROW_ID, "group": "working_analysis",
                     "name": "Archived analysis outputs",
                     "words": f'{n} gated output{"" if n == 1 else "s"} from this run, '
                              f'kept beside the monitoring model in this borrower\'s '
                              f'monitoring folder',
                     "dated": f'{_pretty_period(payload["identity"].get("period"))} run',
                     "url": None, "linked": False})
    order = {g: i for i, (g, _w) in enumerate(FILE_GROUP_WORDS)}
    return sorted(rows, key=lambda d: (order.get(d.get("group"), len(order)),
                                       str(d.get("name"))))


def render_files_registry(payload, keyed, empties):
    rows = registry_rows(payload)
    if not rows:
        return absent_note({"what": "The source-document registry",
                            "reason": "this run names no document it rests on."})
    out, seen = [], None
    for d in rows:
        if d.get("group") != seen:
            seen = d.get("group")
            head = dict(FILE_GROUP_WORDS).get(seen, str(seen).replace("_", " ").title())
            out.append(f'      <tr><td class="grp" colspan="3">{head}</td></tr>')
        name = esc(d.get("name"))
        if d.get("url"):
            cell = (f'<a class="doclink" target="_blank" rel="noopener" '
                    f'href="{esc(d["url"])}">{name}</a>')
        else:
            cell = name
        if d.get("words"):
            cell += f' <span class="small muted">&mdash; {esc(d["words"])}</span>'
        elif not d.get("url"):
            cell += (' <span class="small muted">&mdash; named in this report; the '
                     'folder it sits in is not one this memo can open</span>')
        note = keyed_cell(keyed, "DOCUMENT_NOTES", str(d.get("id")), empties)
        dated = d.get("dated")
        dated_cell = ("&mdash;" if not dated else
                      f'<span class="small">{esc(_pretty_when(dated))}</span>')
        out.append(f'      <tr><td>{cell}</td><td>{note}</td>'
                   f'<td>{dated_cell}</td></tr>')
    return ('  <table>\n'
            '    <thead><tr><th style="width:32%">Document</th><th>What it is</th>'
            '<th style="width:16%">Dated / covers</th></tr></thead>\n'
            '    <tbody>\n' + "\n".join(out) + '\n    </tbody>\n  </table>')


# The families the run's own notes fall into, in the order the tab reads them, with
# the words a credit reader gets for the whole family.
NOTE_FAMILIES = [
    ("links", "Some files this memo names cannot be opened"),
    ("plan", "No plan on file to compare against"),
    ("model", "Lines the monitoring workbook could not settle"),
    ("chart", "Lines missing from the trend chart"),
    ("figure", "Figures held back because their unit is not stated"),
    ("restated", "Months the borrower restated"),
]
_RESTATED_MOVE = re.compile(r"from \$(-?[\d,]+) to \$(-?[\d,]+)")
# Below this many members a family prints each note: two or three link gaps are
# different things and collapsing them loses the answer. At or above it the
# repetition IS the problem -- forty-four restatement lines bury the tab -- so the
# family collapses to one note that says how many there are and how big they get,
# with the detail in the trace. Four keeps a borrower's card inside the five-to-
# eight notes the format asks for.
COLLAPSE_AT = 4
TRACE_MEMBERS = 8


def _family_of(note_id):
    return str(note_id or "").split(".")[0]


def _restated_summary(members):
    """Forty-four restatement lines as one note a reader can act on."""
    months, biggest, big_move = [], None, 0.0
    for m in members:
        lead = str(m.get("lead") or "")
        month = lead.split(" was restated")[0].strip()
        if month:
            months.append(month)
        hit = _RESTATED_MOVE.search(str(m.get("text") or ""))
        if hit:
            try:
                a = float(hit.group(1).replace(",", ""))
                b = float(hit.group(2).replace(",", ""))
            except ValueError:
                continue
            if abs(b - a) >= big_move:
                big_move, biggest = abs(b - a), (month, m.get("text"))
    uniq = sorted(set(months), key=_month_key)
    said = (", ".join(uniq[:-1]) + " and " + uniq[-1]) if len(uniq) > 1 else \
        (uniq[0] if uniq else "earlier months")
    line = (f'The borrower\'s later reporting restates {len(members)} figures across '
            f'{len(uniq)} month{"" if len(uniq) == 1 else "s"} &mdash; {said}. The '
            f'later package is the governing figure, so a comparison that reaches '
            f'back into those months reads differently against an earlier copy of '
            f'the same statement.')
    if biggest and big_move:
        line += (f' The largest single change is {esc(biggest[0])}, '
                 f'{dollars(big_move)}.')
    return line


def _withheld_summary(members):
    names = []
    for m in members:
        t = str(m.get("text") or "")
        names.append(t.split(" is not shown")[0].strip() or "a figure")
    names = sorted(set(names))
    said = (", ".join(names[:-1]) + " and " + names[-1]) if len(names) > 1 else names[0]
    return (f'{len(members)} figures are not shown: {esc(said)}. The record behind '
            f'each of them does not say whether it is a money amount, and this '
            f'borrower\'s statements are not kept in whole dollars, so stating one '
            f'either way could be wrong by a factor of a thousand. Every figure the '
            f'memo does show states its unit.')


FAMILY_SUMMARY = {
    "links": "{n} of the files this memo names cannot be opened from here. Every "
             "figure is unaffected — each still names the file it was read from, "
             "and the trace lists the cases.",
    "model": "{n} lines in the monitoring workbook could not be settled: a name more "
             "than one row uses, or a line the workbook does not carry. A figure that "
             "needed one of them is left off rather than shown as zero.",
    "chart": "{n} lines are missing from the trend chart. The months it does draw are "
             "the ones the workbook carries for the rest of the series.",
    "plan": "{n} notes about the plan this month is measured against; the trace lists "
            "them.",
}


def _generic_summary(fam, members):
    words = FAMILY_SUMMARY.get(fam)
    if words:
        return words.format(n=len(members))
    return f'{len(members)} cases of the same kind; the trace lists them.'


def render_data_quality(payload):
    """The run's own mechanical notes, grouped so the count never buries the point.

    A note per affected figure is how a real gap becomes invisible: forty-four
    restatement lines and six withheld figures push the two notes a reader has to
    act on off the bottom of the card. So a family with several members prints ONE
    note that says how many there are and how big they get, and carries every member
    in its trace -- nothing is dropped, and nothing is repeated forty-four times.
    """
    notes = payload["sections"]["data_quality"].get("notes") or []
    if not notes:
        return ('    <li>Every figure in this memo traces to the borrower\'s own '
                'reporting for the month.</li>')
    grouped = {}
    for n in notes:
        grouped.setdefault(_family_of(n.get("id")), []).append(n)
    known = [f for f, _w in NOTE_FAMILIES]
    families = [f for f, _w in NOTE_FAMILIES if f in grouped] + \
               sorted(f for f in grouped if f not in known)
    words = dict(NOTE_FAMILIES)
    out = []
    for fam in families:
        members = grouped[fam]
        if len(members) < COLLAPSE_AT:
            for m in members:
                out.append(f'    <li><b>{esc(m.get("lead"))}</b> '
                           f'&mdash; {esc(m.get("text"))}</li>')
            continue
        head = words.get(fam, str(fam).replace("_", " ").capitalize())
        if fam == "restated":
            body = _restated_summary(members)
        elif fam == "figure":
            body = _withheld_summary(members)
        else:
            body = _generic_summary(fam, members)
        steps = [{"l": str(m.get("text") or ""), "v": ""}
                 for m in members[:TRACE_MEMBERS]]
        rest = len(members) - len(steps)
        if rest > 0:
            steps.append({"l": f'and {rest} more of the same kind', "v": "",
                          "res": True})
        traced = trc_raw(f'<b>{head}</b>',
                         f'{len(members)} cases, each listed below.', [], steps)
        out.append(f'    <li>{traced} &mdash; {body}</li>')
    out.extend(_unhoused_gaps(payload))
    return "\n".join(out)


def _unhoused_gaps(payload):
    """Gaps the run recorded that no other block on the page has a place for.

    A block states its own absences where it can: a covenant not tested this month
    gets an "n/e" cell, a section with nothing to draw says so in its own words. What
    is left over -- a basis break, a certificate whose signer was never captured, a
    reporting package the model's own record does not name -- would otherwise reach
    no reader at all, and a gap nobody is told about reads exactly like no gap. This
    card is where the run says what it could not do, so they end here.
    """
    housed = set()
    for sec in payload["sections"].values():
        for key in ("figures", "chips", "groups", "table", "history", "grid"):
            if sec.get(key):
                housed.add(key)
    shown = {str(n.get("id") or "") for n in
             (payload["sections"]["data_quality"].get("notes") or [])}
    out = []
    for rec in payload.get("absent") or []:
        kind = str(rec.get("kind") or "")
        if kind in _GAPS_SHOWN_ELSEWHERE or rec.get("id") in shown:
            continue
        what = str(rec.get("what") or "").strip()
        why = str(rec.get("reason") or "").strip()
        if not what and not why:
            continue
        lead = what or "This run could not cover everything"
        body = why or "the run recorded it as not covered."
        out.append(f'    <li><b>{esc(lead)}</b> &mdash; {esc(body)}</li>')
    return out


# Kinds whose own block already tells the reader: a covenant with no test this
# month shows "n/e" in the history, a skipped module and an unavailable section
# state themselves where they would have been, a withheld figure and a restated
# month are already a data-quality note.
_GAPS_SHOWN_ELSEWHERE = {
    "covenant_not_tested", "covenant_no_test_this_month", "module_skipped",
    "section_unavailable", "table_absent", "series_absent", "ambiguous_row",
    "unit_unstated", "figure_absent", "line_not_found", "column_absent",
    "column_partial", "partial_months", "coverage_incomplete", "runway_not_measured",
    "facility_absent", "restated", "document_not_linked", "budget_absent",
}


FOOTER_GROUP_WORDS = {
    "loan_documentation": "the executed loan documents",
    "underwriting": "the underwriting memo",
    "monthly_reporting": "the monthly reporting packages with the compliance "
                         "certificate",
    "budget": "the pinned budget",
}


def render_footer_sources(payload):
    """Which kinds of file this deal's memo rests on, in one clause."""
    docs = payload["sections"]["files_registry"].get("documents") or []
    present = {d.get("group") for d in docs}
    words = dict(FOOTER_GROUP_WORDS)
    if not any(str(d.get("id")) == "monthly_reporting.compliance_certificate"
               for d in docs):
        words["monthly_reporting"] = "the monthly reporting packages"
    said = [words[g] for g, _w in FILE_GROUP_WORDS
            if g in present and g in words]
    if not said:
        return "the files this borrower's monitoring folder carries"
    if len(said) == 1:
        return said[0]
    return ", ".join(said[:-1]) + " and " + said[-1]


# ============================================================ the prose plumbing
def keyed_ids(payload):
    """Which ids each keyed box is expected to carry on THIS run."""
    cov = [str(g.get("id")) for g in
           (payload["sections"]["covenant_detail"].get("grid") or [])]
    caps = [str(r.get("id")) for r in
            (payload["sections"]["deal_capstructure"].get("rows") or [])]
    docs = [str(d.get("id")) for d in registry_rows(payload)]
    return {"COVENANT_DELTAS": cov, "CAPSTRUCTURE_NOTES": caps,
            "DOCUMENT_NOTES": docs}


def keyed_cell(keyed, box, row_id, empties):
    """One keyed sentence, or the marked dash that says nobody wrote it."""
    text = (keyed.get(box) or {}).get(row_id)
    if text:
        return text
    empties.append(f"{box}[{row_id}]")
    return f'<span class="muted" data-box="{esc(box)}">&mdash;</span>'


def empty_box(name):
    sentence = EMPTY_SENTENCE
    context = BOX_CONTEXT.get(name, "block")
    if context == "inline":
        return f'<span class="small muted" data-box="{name}">{sentence}</span>'
    marker = f'<p class="small muted" data-box="{name}">{sentence}</p>'
    return f'<li>{marker}</li>' if context == "list" else marker


# ------------------------------------------------------------------ the render
def manifest_tokens(template):
    return sorted(set(TOKEN.findall(template)))


def plugin_version(stated=None):
    if stated:
        return stated
    try:
        with open(PLUGIN_JSON, encoding="utf-8") as fh:
            return str(json.load(fh).get("version") or "unknown")
    except (OSError, ValueError):
        return "unknown"


def render(payload, prose, template, run_date, version=None):
    """(html, empty box names). Raises RenderError on an input the operator can fix."""
    tokens = manifest_tokens(template)
    allowed = set(PROSE_TOKENS) | set(KEYED_PROSE)
    # Keys opening with "_" are notes riding along in prose.json — the contract
    # block --prose-keys writes, a comment — never boxes, so they render nothing.
    unknown = sorted(k for k in set(prose) - allowed if not k.startswith("_"))
    if unknown:
        raise RenderError(
            "prose.json carries " + ", ".join(unknown) + ", which the memo's format "
            "does not have a place for. The keys it takes are: "
            + ", ".join(PROSE_TOKENS + KEYED_PROSE) + ".")
    expected = keyed_ids(payload)
    keyed, bad = {}, []
    for box in KEYED_PROSE:
        given = prose.get(box) or {}
        if not isinstance(given, dict):
            raise RenderError(f"prose.json's {box} has to be a set of ids and their "
                              f"sentences, one per row of the table it fills.")
        for rid in sorted(given):
            if rid not in expected[box]:
                bad.append(f"{box}[{rid}]")
        keyed[box] = given
    if bad:
        raise RenderError(
            "prose.json names " + ", ".join(bad) + ", which this run has no row for. "
            "Run --prose-keys against this payload to see the ids it expects.")

    stores = payload.get("stores") or {}
    empties = []
    strip = render_strip(payload)
    values = {
        "TITLE": render_title(payload),
        "H1_BORROWER": render_h1(payload),
        "SUB_LINE": render_sub_line(payload, run_date, plugin_version(version)),
        "DEAL_TEAM": render_deal_team(payload),
        "PILLS": render_pills(payload),
        "SUMMARY_UPDATE_LABEL": render_update_label(payload),
        "COVENANT_STRIP": strip,
        "COVENANT_STRIP_REPEAT": strip,
        "SNAP_PERFORMANCE_TEASER": _performance_teaser(payload),
        "PERF_DATA": json.dumps(
            payload["sections"]["snapshot_performance"].get("performance_views")
            or {"views": []}, sort_keys=True),
        "SNAP_COLLATERAL_TEASER": _collateral_teaser(payload),
        "SNAP_COLLATERAL": render_collateral(payload),
        "TREND_RANGE": render_trend_range(payload),
        "TREND_DATA": render_trend_data(payload),
        "TREND_CAPTION": render_trend_caption(payload),
        "DEAL_COMPANY_FACTS": render_company_facts(payload),
        "DEAL_TERMS": render_deal_terms(payload, stores),
        "DEAL_TRANSACTION_GAPS": render_transaction_gaps(payload),
        "DEAL_TRANSACTION_SOURCES": render_transaction_sources(payload, stores),
        "DEAL_SECURITY_ASOF": render_security_asof(payload),
        "DEAL_SECURITY_TABLE": render_security_table(payload, stores),
        "DEAL_CAPSTRUCTURE": render_capstructure(payload, stores, keyed, empties),
        "COVENANT_PREAMBLE": render_preamble(payload, stores),
        "COVENANT_GRID": render_covenant_grid(payload, stores, keyed, empties),
        "STRESS_FACTS": render_stress_facts(payload, stores),
        "COVENANT_HISTORY": render_covenant_history(payload),
        "COVENANT_FAROUT": render_farout(payload, stores),
        "IS_BASIS": _basis_line(payload, "income_review", "flow"),
        "IS_TABLE": render_table(payload, "income_review", "Income statement"),
        # The heading only claims revenue quality when the block is on the page.
        "IS_RQ_SUFFIX": (' <span class="small muted" style="font-weight:400">'
                         '· incl. revenue quality</span>'
                         if payload["sections"]["income_review"]
                         .get("revenue_quality") else ""),
        "REVENUE_QUALITY": render_revenue_quality(payload, stores),
        "CF_BASIS": _basis_line(payload, "cashflow_review", "cashflow"),
        "CF_TABLE": render_table(payload, "cashflow_review", "Cash flow"),
        "BS_BASIS": _basis_line(payload, "balance_review", "balance"),
        "BS_TABLE": render_table(payload, "balance_review", "Balance sheet"),
        "LIQUIDITY": render_liquidity(payload, stores),
        "AGING": render_aging(payload, stores),
        "FILES_REGISTRY": render_files_registry(payload, keyed, empties),
        "DATA_QUALITY": render_data_quality(payload),
        "FOOTER_SOURCES": render_footer_sources(payload),
    }
    # The summary card is the page the deal team reads first, so its five blocks
    # are held to their format here rather than discovered broken on the page:
    # each is prose inside a labelled paragraph (block markup would split it),
    # and a block that reads figures must carry at least one.
    badsum = []
    for t in SUMMARY_BOXES:
        written = str(prose.get(t) or "").strip()
        if not written:
            continue
        if re.search(r"<\s*(?:li|ul|ol|p|h\d|div|table)\b", written, re.I):
            badsum.append(f"{t} carries list or block markup; it is a short prose "
                          "paragraph inside a labelled sentence")
        elif t in SUMMARY_NEED_FIGURE and not re.search(r"\d", written):
            badsum.append(f"{t} states no figure; a summary block is a read of "
                          "this period's numbers")
    if badsum:
        raise RenderError("the executive summary does not follow its format: "
                          + "; ".join(badsum) + ". The block contract is in the "
                          "prose file's _contract.summary_blocks.")

    for t in PROSE_TOKENS:
        written = str(prose.get(t) or "").strip()
        if written:
            values[t] = prose[t]
            continue
        # Two boxes annotate a block that only exists when its module ran, and one
        # is a list of questions a month may not have: those render nothing rather
        # than telling the reader an absent block was left unwritten.
        gate = COLLAPSE_EMPTY.get(t, "__missing__")
        if gate == "__missing__":
            empties.append(t)
            values[t] = empty_box(t)
        elif gate is None or not str(values.get(gate) or "").strip():
            values[t] = ""
        else:
            empties.append(t)
            values[t] = empty_box(t)
    missing = [t for t in tokens if t not in values]
    if missing:
        raise RenderError(
            "the template declares " + ", ".join(missing) + " and the renderer has "
            "nothing to put there. Either the manifest gained an entry or the payload "
            "lost a key.")
    return TOKEN.sub(lambda m: values[m.group(1)], template), empties


def prose_contract(payload, template_path):
    """The whole prose contract, riding inside the --prose-keys file.

    The contract keeps writers from inspecting implementation files to learn
    what the keys already know: which box takes
    <li> items, how a deep link is written, which open items the gate will demand
    back. The writer edits the one file this prints, so the contract lives in it.
    """
    kinds = {"list": [], "inline": [], "block": []}
    for box, ctx in BOX_CONTEXT.items():
        kinds[ctx].append(box)
    contract = {
        "how": "Fill every key; values are HTML. Leave a box \"\" when the month "
               "gives it nothing, and say so in your report. Keys opening with "
               "\"_\" (this note) are ignored by the renderer.",
        "list_boxes": {"boxes": sorted(kinds["list"]),
                       "value": "a string of <li>…</li> items and nothing outside "
                                "them"},
        "sentence_boxes": {"boxes": sorted(kinds["inline"]),
                           "value": "sentences that sit inside an existing "
                                    "paragraph — inline tags like <b> only, no "
                                    "<p> or <ul>"},
        "paragraph_boxes": {"boxes": sorted(kinds["block"]),
                            "value": "one or two short paragraphs"},
        "keyed_boxes": {
            "COVENANT_DELTAS": "per covenant id: one clause on WHY the certified "
                               "figure and ours differ — the component that carries "
                               "the gap, named. Our reconstruction governs; do not "
                               "write that the borrower's basis governs, whichever "
                               "way the gap runs",
            "CAPSTRUCTURE_NOTES": "per row id: the instrument's pricing or terms "
                                  "in one clause",
            "DOCUMENT_NOTES": "per document id: what the document is and what it "
                              "settles, one sentence"},
        "summary_blocks": {
            "what": "the executive summary is the analyst's one-pager for the "
                    "deal team: five labelled prose blocks, each 2-5 sentences, "
                    "written the way a portfolio review reads — the figure, its "
                    "comparator (vs Budget, vs prior year), then the driver. The "
                    "template owns the labels; write only the prose.",
            "cadence": "the renderer titles the first block from the period: a "
                       "March/June/September/December period reads 'Quarterly "
                       "update' and its prose covers the quarter and the year so "
                       "far; any other month reads 'Monthly update' and covers "
                       "the month and the year so far",
            "SUMMARY_UPDATE": "revenue and EBITDA vs Budget and prior year — the "
                              "period first, the cumulative line second — then "
                              "the driver: which segment or brand moved, mix or "
                              "margin, at the finest level the package reports",
            "SUMMARY_LIQUIDITY": "period-end cash against the floor, where the "
                                 "cash came from (earned, borrowed, working "
                                 "capital), months of runway when burning, and "
                                 "the expected direction with its basis",
            "SUMMARY_COLLATERAL": "pledged components and their total, coverage "
                                  "against funded debt, and the prior-year or "
                                  "plan comparison where one is on file",
            "SUMMARY_COVENANTS": "one sentence when everything passes with room; "
                                 "otherwise the failing or governing leg, the "
                                 "certified-vs-model gap, and the nearest tight "
                                 "test with what clearing it takes",
            "SUMMARY_OUTLOOK": "the forward indicators on file (backlog, order "
                               "book, seasonality), the governing question for "
                               "the credit, and the open asks — never a concern "
                               "manufactured to fill the space",
            "numbers": "round to $X.XM / $XXXK in these five blocks; full "
                       "precision lives in the tables, where every figure is "
                       "click-traceable",
            "bad_news": "a miss, a breach or a stretch is written in its home "
                        "block with the same syntax as a beat — there is no "
                        "separate red-flags list, and a fired concern must not "
                        "be left out of its block"},
        "deep_links": 'a reference to another section is written '
                      '<a class="jumpname" href="#sec-covenant">Covenant detail '
                      '<span class="arw">&#8599;</span></a> — same markup, any '
                      'section id below',
        "language": "lead with the fact and its figure; the banned words and "
                    "patterns are in references/report-structure.md under the "
                    "language contract",
    }
    try:
        with open(template_path, encoding="utf-8") as fh:
            contract["section_ids"] = sorted(
                set(re.findall(r'id="(sec-[a-z0-9-]+)"', fh.read())))
    except OSError:
        pass
    items = (((payload or {}).get("sections") or {}).get("flags") or {}) \
        .get("items") or []
    if items:
        import validate_memo
        needles = []
        for item in items:
            token, forms = validate_memo._flag_needle(item)
            if not token:
                continue
            need = {"write_one_of": sorted(set(forms))}
            if item.get("amount_usd"):
                need["or_its_amount"] = ("any figure within 1% of "
                                         f"{abs(round(float(item['amount_usd']))):,}")
            needles.append(need)
        contract["flags_must_name"] = {
            "rule": "the FLAGS box covers every open item; the gate looks for "
                    "each item's identifying token (or its dollar amount) in "
                    "your prose",
            "items": needles}
    return contract


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--payload")
    ap.add_argument("--prose")
    ap.add_argument("--out")
    ap.add_argument("--template", default=DEFAULT_TEMPLATE)
    ap.add_argument("--run-date", help="the date the memo was prepared, YYYY-MM-DD")
    ap.add_argument("--plugin-version",
                    help="the plugin version to state; read from the plugin otherwise")
    ap.add_argument("--empty-boxes", choices=("allow", "refuse"), default="allow",
                    help="refuse: stop and list every text box left unwritten")
    ap.add_argument("--prose-keys", action="store_true",
                    help="print the keys prose.json may carry, and stop")
    a = ap.parse_args()
    if a.prose_keys:
        out = {t: "" for t in PROSE_TOKENS}
        seeded = {box: {} for box in KEYED_PROSE}
        payload = None
        if a.payload:
            try:
                with open(a.payload, encoding="utf-8") as fh:
                    payload = json.load(fh)
                ids = keyed_ids(payload)
                seeded = {box: {rid: "" for rid in ids[box]} for box in KEYED_PROSE}
            except (OSError, ValueError, KeyError) as e:
                print(f"could not read the payload: {e}", file=sys.stderr)
                return 1
        out.update(seeded)
        out["_contract"] = prose_contract(payload, a.template)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    for name in ("payload", "prose", "out", "run_date"):
        if not getattr(a, name):
            print(f"--{name.replace('_', '-')} is required", file=sys.stderr)
            return 2
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.run_date):
        print("--run-date is a calendar date, written YYYY-MM-DD", file=sys.stderr)
        return 2
    try:
        with open(a.template, encoding="utf-8") as fh:
            template = fh.read()
        with open(a.payload, encoding="utf-8") as fh:
            payload = json.load(fh)
        relink(payload, payload.get("stores") or {})
        with open(a.prose, encoding="utf-8") as fh:
            prose = json.load(fh)
        out, empties = render(payload, prose, template, a.run_date,
                              a.plugin_version)
    except RenderError as e:
        print(str(e), file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        print(f"could not read an input: {e}", file=sys.stderr)
        return 1
    left = [t for t in manifest_tokens(out) if t not in ASSET_TOKENS]
    if left:
        print("the memo still has unfilled places: " + ", ".join(left), file=sys.stderr)
        return 1
    if empties and a.empty_boxes == "refuse":
        print("these text boxes were left unwritten: " + ", ".join(sorted(empties)),
              file=sys.stderr)
        return 1
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(out)
    said = f" — {len(empties)} text box(es) unwritten" if empties else ""
    print(f"OK — memo written to {a.out}{said}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
