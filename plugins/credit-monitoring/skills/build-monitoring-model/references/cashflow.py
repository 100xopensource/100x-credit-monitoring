"""cashflow.py -- deterministic indirect cash-flow derivation for build-monitoring-model.

Reporting packages routinely ship an Income Statement + Balance Sheet but NO cash-flow statement.
This builds a full indirect CF (Operating / Investing / Financing + Net Change) from the IS + BS,
tied BY CONSTRUCTION to the balance-sheet cash change (a disclosed `unexplained_plug` absorbs any
residual from incomplete line classification, so Net Change always equals the BS cash delta).

Pure + testable: `build_cashflow(bs, is_, months)` takes {norm_label: [monthly values]} dicts.
`from_model(model)` adapts a monitor_lib Model. `write_sheet(wb, cf)` emits the layout-contract sheet.
Method (per month t): OCF = NetIncome + D&A - Δ(operating assets) + Δ(operating liabilities);
ICF = -(Δ(non-current assets, net) + D&A)  [capex, D&A reversed out of OCF]; FCF = Δ(debt) +
(Δ(equity) - NetIncome)  [external equity / distributions]; plug = ΔCash_BS - (OCF+ICF+FCF)."""
import re

def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None

_SKIP = ("total", "memo", "net ")
def _skip(l):
    return l.startswith(_SKIP) or "liabilities & equity" in l or "balance check" in l or "liabilities and equity" in l

# ordered classification; FIRST match wins (order matters: debt before assets for 'vehicle', etc.)
_DEBT_FALSE_FRIENDS = ("bad debt", "debt service reserve", "deferred debt",
                       "unamortized debt", "debt discount", "debt issuance")
_DEBT_RULES = [r"\bdebt\b", r"note[s]? payable", r"\bloan\b", r"convertible", r"paid in kind", r"\bpik\b",
               r"lease liabilit", r"line of credit", r"revolver", r"origination cost", r"\bnotes\b"]

_RULES = [
    ("cash",    [r"\bcash\b", r"checking", r"money market", r"\bbank\b", r"undeposited"]),
    ("debt",    _DEBT_RULES),
    ("equity",  [r"equity", r"retained earnings", r"distribution", r"contribution", r"capital",
                 r"profits interest", r"member", r"owner"]),
    ("op_liab", [r"payable", r"accrued", r"customer deposit", r"deferred", r"sales tax", r"warranty",
                 r"gift card", r"credit card", r"\bpto\b", r"401k", r"received not billed",
                 r"tax payable", r"unapplied", r"other current liab"]),
    ("nc_asset",[r"fixed asset", r"equipment", r"machinery", r"mold", r"vehicle", r"leasehold",
                 r"construction", r"intangible", r"right of use", r"right-of-use", r"\brou\b",
                 r"accumulated depreciation", r"accumulated amortization", r"security deposit",
                 r"goodwill", r"furniture", r"website", r"mobile app", r"hardware", r"\berp\b"]),
    ("op_asset",[r"receivable", r"inventory", r"prepaid", r"vendor prepay", r"other current asset",
                 r"deposit"]),
]
def _only_false_friend(l):
    stripped = l
    found = False
    for term in _DEBT_FALSE_FRIENDS:
        if term in stripped:
            stripped = stripped.replace(term, "")
            found = True
    return found and not any(re.search(pattern, stripped) for pattern in _DEBT_RULES)


def classify(label):
    l = label.lower()
    if _only_false_friend(l):
        return "unclassified"
    for bucket, pats in _RULES:
        if any(re.search(p, l) for p in pats):
            return bucket
    return "unclassified"

def _region_marker(l):
    """Return the balance-sheet region a header row starts, else None. Order matters."""
    if "fixed asset" in l or "other asset" in l: return "nc_asset"
    if "long term liab" in l or "long-term liab" in l: return "lt_liab"
    if "current liab" in l: return "current_liab"
    if "current asset" in l or l == "assets": return "current_assets"
    if l == "equity": return "equity"
    return None

def _bucket_for_region(l, region):
    """Section-based bucket; falls back to keyword classify() when no section context (clean fixtures)."""
    if region == "current_assets":
        return "cash" if any(k in l for k in ("cash", "checking", "money market")) else "op_asset"
    if region == "nc_asset":
        return "nc_asset"
    if _only_false_friend(l):
        return "unclassified"
    if region == "current_liab":
        return "debt" if classify(l) == "debt" else "op_liab"
    if region == "lt_liab":
        return "debt"
    if region == "equity":
        return "equity"
    return classify(l)

def _delta(series, t):
    # series is always aligned to the model's month axis (from_model/build_cashflow
    # guarantee len == n), so t-1 and t are in range for every t in 1..n-1.
    a = _f(series[t - 1]) or 0.0
    b = _f(series[t]) or 0.0
    return b - a

def build_cashflow(bs, is_, months):
    """bs/is_: {norm_label: [values aligned to months]}. Returns dict with per-month CF rows + meta."""
    n = len(months)
    # bucket balance-sheet lines
    buckets = {k: [] for k in ("cash","op_asset","op_liab","nc_asset","debt","equity","unclassified")}
    region = None
    for lab, vals in bs.items():   # dict preserves balance-sheet row order
        l = lab.strip().lower()
        if _skip(l):
            continue
        fv = [_f(v) for v in vals]
        has_data = any(v is not None for v in fv)
        mk = _region_marker(l)
        if mk is not None and not has_data:
            region = mk            # a section header carries no data — it only sets context
            continue
        buckets[_bucket_for_region(l, region)].append((lab, fv))
    # income-statement inputs (values pre-parsed to float|None once here)
    is_items = [(lab, [ _f(x) for x in vals ]) for lab, vals in is_.items()]

    # Net income: tolerate label variants ("NET INCOME (LOSS)", "Net Income (Loss)")
    # by dropping ONLY a trailing (loss)-style qualifier before an exact compare --
    # not arbitrary parentheticals like "(pre-tax)"/"(GAAP)", which denote a
    # DIFFERENT subtotal that must not be mistaken for bottom-line net income.
    def _norm_ni(lab):
        s = " ".join(lab.lower().split())
        return re.sub(r"\s*\([^)]*loss[^)]*\)\s*$", "", s).strip()
    ni_hits = [(lab, v) for lab, v in is_items if _norm_ni(lab) == "net income"]
    ni_matches = [lab for lab, _v in ni_hits]
    ni = ni_hits[0][1] if ni_hits else None
    ni_warning = None
    if ni is None:
        ni = [None] * n
        ni_warning = ("no Net Income row found on the Income Statement; the derived cash "
                      "flow starts from zero net income")
    elif len(ni_matches) > 1:
        ni_warning = f"more than one Net Income row matched ({ni_matches}); used the first"

    # D&A: sum the depreciation/amortization lines -- but when a COMBINED
    # "depreciation & amortization" line coexists with separate depreciation/
    # amortization lines, use only the combined line (summing all double-counts).
    def _is_da(lab):
        l = lab.lower()
        return (("depreciation" in l or "amortization" in l)
                and "accumulated" not in l and not l.strip().startswith("total"))
    da_labels = [lab for lab, v in is_items if _is_da(lab)]
    da_combined = [lab for lab in da_labels
                   if "depreciation" in lab.lower() and "amortization" in lab.lower()]
    da_use = set(da_combined) if da_combined else set(da_labels)
    da = [sum((v[i] or 0.0) for lab, v in is_items if lab in da_use) for i in range(n)]
    da_warning = None
    if not da_labels:
        da_warning = ("no depreciation/amortization row found on the Income Statement; "
                      "D&A treated as zero in the derived cash flow")
    elif da_combined and len(da_labels) > len(da_combined):
        singles = [lab for lab in da_labels if lab not in da_use]
        da_warning = (f"used the combined depreciation & amortization line(s) {da_combined} "
                      f"and ignored separate line(s) {singles} to avoid double-counting")
    elif len(da_labels) > 1:
        da_warning = (f"summed multiple depreciation/amortization lines {da_labels}; "
                      f"verify they are distinct and not double-counted")

    def bucket_delta(name, t):
        return sum(_delta(v, t) for _, v in buckets[name])

    rows = {k: [None] * n for k in
            ["Net Income","Depreciation & Amortization (non-cash)","Change in Working Capital",
             "Total Operating Activities","Investing Activities (derived)","Financing Activities (derived)",
             "Non-cash Unexplained / plug unexplained (plug)","Net Change in Cash for Period","memo: Change in cash per Balance Sheet"]}
    meta = {"unclassified_lines": sorted(l for l, _ in buckets["unclassified"]),
            "max_abs_plug": 0.0, "method": "indirect; ties to BS cash by construction (plug discloses residual)"}
    if ni_warning:
        meta["ni_warning"] = ni_warning
    if da_warning:
        meta["da_warning"] = da_warning
    for t in range(1, n):
        d_op_assets = bucket_delta("op_asset", t)
        d_op_liab = bucket_delta("op_liab", t)
        d_nc = bucket_delta("nc_asset", t)
        d_debt = bucket_delta("debt", t)
        d_eq = bucket_delta("equity", t)
        d_cash = bucket_delta("cash", t)
        nit = _f(ni[t]) or 0.0
        dat = da[t] or 0.0
        wc = -d_op_assets + d_op_liab
        ocf = nit + dat + wc
        icf = -(d_nc + dat)
        fcf = d_debt + (d_eq - nit)
        net = ocf + icf + fcf
        plug = d_cash - net
        rows["Net Income"][t] = round(nit, 2)
        rows["Depreciation & Amortization (non-cash)"][t] = round(dat, 2)
        rows["Change in Working Capital"][t] = round(wc, 2)
        rows["Total Operating Activities"][t] = round(ocf, 2)
        rows["Investing Activities (derived)"][t] = round(icf, 2)
        rows["Financing Activities (derived)"][t] = round(fcf, 2)
        rows["Non-cash Unexplained / plug unexplained (plug)"][t] = round(plug, 2)
        rows["Net Change in Cash for Period"][t] = round(d_cash, 2)
        rows["memo: Change in cash per Balance Sheet"][t] = round(d_cash, 2)
        meta["max_abs_plug"] = max(meta["max_abs_plug"], abs(plug))
    return {"rows": rows, "meta": meta, "buckets": {k: [l for l, _ in v] for k, v in buckets.items()}}

def from_model(model):
    bs = {lab: list(vals) for lab, vals in (getattr(model, "sheets", {}).get("Balance Sheet") or {}).items()}
    is_ = {lab: list(vals) for lab, vals in (getattr(model, "sheets", {}).get("Income Statement") or {}).items()}
    return build_cashflow(bs, is_, list(model.months))

def write_sheet(wb, cf, months, sheet="Cash Flow Statement"):
    ws = wb.create_sheet(sheet) if sheet not in wb.sheetnames else wb[sheet]
    ws["A1"] = "Cash Flow Statement (DERIVED, indirect)"
    ws["A6"] = "Financial Row"
    for j, m in enumerate(months):
        ws.cell(row=6, column=2 + j, value=m)
    r = 7
    for lab, series in cf["rows"].items():
        ws.cell(row=r, column=1, value=lab)
        for j, v in enumerate(series):
            if v is not None:
                ws.cell(row=r, column=2 + j, value=v)
        r += 1
    return ws
