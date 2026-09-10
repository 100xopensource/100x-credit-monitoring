"""trend_signals.py -- scale-aware relational & ratio-trend detection for three-statement-analysis.

ADDITIVE, DETERMINISTIC. Produces three output blocks the rigid per-leaf line read misses:
  * ratio_trends              -- DIO/DSO/DPO/CCC/current_ratio/margins trended MONTHLY (not spot),
                                 flagged when they deteriorate over the window.
  * working_capital_divergence-- a WC asset (inventory / AR) building while revenue/COGS falls.
  * reclassifications         -- an offsetting move between sibling lines (e.g. debt short<->long)
                                 that nets ~flat at the group level (a whole balance moving classes).

Thresholds are SCALE-AWARE: primary gate = max(scale dollar floor `min_abs`, k_vol * the series'
own trailing volatility [median abs MoM delta]); fixed % values are FALLBACKS used only when
volatility is undefined (too little history). Every gate is disclosed in the returned `params`.
Ratio formulas MATCH the engine's spot definitions (per-month day count). Tunables live in
config.analysis.trend {window, trend_min_months, k_vol, ratio_rel, wc_driver_pct, wc_rev_pct,
reclass_net_frac}. Never fatal: callers wrap in try/except."""
import calendar, datetime, re, statistics

_QUAL = re.compile(r"\((?:st|lt|short[- ]?term|long[- ]?term|current|non[- ]?current)[^)]*\)", re.I)
_LEAD_ACCT = re.compile(r"^\s*\d{3,6}\s*[-:]\s*")
_CLASS_TOKENS = re.compile(r"\b(st|lt|short[- ]?term|long[- ]?term|current|non[- ]?current|portion)\b", re.I)

def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None

def _days_in(label):
    try:
        d = datetime.datetime.strptime(str(label).strip(), "%b %Y")
        return calendar.monthrange(d.year, d.month)[1]
    except Exception:
        return 30

def _mad(vals):
    """Median absolute MoM delta over consecutive non-None values (a robust volatility gauge)."""
    deltas, prev = [], None
    for v in vals:
        if v is not None and prev is not None:
            deltas.append(abs(v - prev))
        if v is not None:
            prev = v
    return statistics.median(deltas) if deltas else None

def _tp(cfg):
    an = (cfg.get("analysis") or {})
    t = dict(an.get("trend") or {})
    return {
        "window": int(t.get("window", 4)),
        "trend_min_months": int(t.get("trend_min_months", 3)),
        "trend_max_months": int(t.get("trend_max_months", 5)),
        "run_end_tolerance": int(t.get("run_end_tolerance", 2)),
        "k_vol": float(t.get("k_vol", 1.5)),
        "ratio_rel": float(t.get("ratio_rel", 0.20)),
        "wc_driver_pct": float(t.get("wc_driver_pct", 0.10)),
        "wc_rev_pct": float(t.get("wc_rev_pct", 0.10)),
        "reclass_net_frac": float(t.get("reclass_net_frac", 0.20)),
        "reclass_min_pct": float(t.get("reclass_min_pct", 0.25)),
    }

# --------------------------------------------------------------------------- ratios
_DAYS = {"dio_days", "dso_days", "dpo_days", "cash_conversion_cycle_days"}
_HIGHER_BAD = _DAYS                       # rising is deterioration
_LOWER_BAD = {"current_ratio", "gross_margin", "ebitda_margin"}  # falling is deterioration

def _ratio_series(S, months, seg, li):
    """Monthly ratio series over the current segment, using the engine's spot definitions."""
    idxs = [i for i in seg if i <= li]
    def g(key, i):
        s = S.get(key); return _f(s[i]) if (s and i < len(s)) else None
    out = {k: [] for k in ["dso_days","dpo_days","dio_days","cash_conversion_cycle_days",
                           "current_ratio","gross_margin","ebitda_margin"]}
    labels = []
    for i in idxs:
        d = _days_in(months[i]); labels.append(months[i])
        rev, cogs = g("revenue", i), g("operating_cogs", i)
        ar, ap, inv = g("accounts_receivable", i), g("accounts_payable", i), g("inventory", i)
        ca, cl = g("total_current_assets", i), g("total_current_liabilities", i)
        gp, eb = g("gross_profit", i), g("ebitda_bridge", i)
        dso = (ar / rev * d) if (ar is not None and rev) else None
        dpo = (ap / cogs * d) if (ap is not None and cogs) else None
        dio = (inv / cogs * d) if (inv is not None and cogs) else None
        ccc = (dso + dio - dpo) if None not in (dso, dio, dpo) else None
        out["dso_days"].append(dso); out["dpo_days"].append(dpo); out["dio_days"].append(dio)
        out["cash_conversion_cycle_days"].append(ccc)
        out["current_ratio"].append((ca / cl) if (ca is not None and cl) else None)
        out["gross_margin"].append((gp / rev) if (gp is not None and rev) else None)
        out["ebitda_margin"].append((eb / rev) if (eb is not None and rev) else None)
    return labels, out

def ratio_trends(S, months, seg, li, tp):
    labels, series = _ratio_series(S, months, seg, li)
    res, exc = {}, []
    w = min(tp["window"], max(0, len(labels) - 1))
    for name, vals in series.items():
        present = [(labels[k], vals[k]) for k in range(len(vals)) if vals[k] is not None]
        if len(present) < max(3, tp["trend_min_months"]):
            continue
        window_vals = [v for _, v in present][-(w + 1):]
        start, latest = window_vals[0], window_vals[-1]
        if start in (None, 0):
            continue
        rel = (latest - start) / abs(start)
        deltas = [window_vals[k] - window_vals[k - 1] for k in range(1, len(window_vals))]
        up = sum(1 for d in deltas if d > 0); down = sum(1 for d in deltas if d < 0)
        run_dir = 1 if up >= tp["trend_min_months"] and down == 0 else (-1 if down >= tp["trend_min_months"] and up == 0 else 0)
        bad_dir = ((latest - start) > 0) if name in _HIGHER_BAD else ((latest - start) < 0)
        deteriorating = bad_dir and (abs(rel) >= tp["ratio_rel"] or run_dir != 0)
        res[name] = {"series": [round(v, 3) for v in window_vals], "latest": round(latest, 3),
                     "window_start": round(start, 3), "window_months": len(window_vals),
                     "rel_change": round(rel, 4), "run_months": (up if run_dir > 0 else down),
                     "direction": ("rising" if latest > start else "falling" if latest < start else "flat"),
                     "deteriorating": bool(deteriorating)}
        if deteriorating:
            unit = "days" if name in _DAYS else "ratio"
            exc.append({"flag": f"ratio_trend_{name}", "severity": "watch",
                        "detail": (f"{name} {res[name]['direction']} {res[name]['window_months']-1}mo "
                                   f"{start:.1f}->{latest:.1f} ({rel:+.0%}) over the window" if unit == "days"
                                   else f"{name} {res[name]['direction']} {start:.1%}->{latest:.1%} ({rel:+.0%}) over the window")})
    return res, exc

# --------------------------------------------------------------------------- WC divergence
def working_capital_divergence(S, months, seg, li, min_abs, tp):
    idxs = [i for i in seg if i <= li]
    w = min(tp["window"], max(0, len(idxs) - 1))
    if w < 2:
        return [], []
    i0, i1 = idxs[-(w + 1)], idxs[-1]
    def mv(key):
        s = S.get(key)
        if not s: return None, None, None
        a, b = _f(s[i0]), _f(s[i1])
        if a is None or b is None: return None, None, None
        return b - a, ((b - a) / abs(a) if a else None), _mad([_f(s[i]) for i in idxs])
    rev_abs, rev_pct, _ = mv("revenue")
    cogs_abs, cogs_pct, _ = mv("operating_cogs")
    sales_down = (rev_pct is not None and rev_pct <= -tp["wc_rev_pct"]) or \
                 (cogs_pct is not None and cogs_pct <= -tp["wc_rev_pct"])
    out, exc = [], []
    if not sales_down:
        return out, exc
    for driver in ("inventory", "accounts_receivable"):
        d_abs, d_pct, d_mad = mv(driver)
        if d_abs is None or d_abs <= 0:
            continue
        gate = max(float(min_abs), tp["k_vol"] * (d_mad or 0.0))
        material = abs(d_abs) >= gate and (d_pct is None or d_pct >= tp["wc_driver_pct"])
        if material:
            rec = {"driver_line": driver, "window": f"{months[i0]}..{months[i1]}",
                   "driver_change_abs": round(d_abs), "driver_change_pct": (round(d_pct, 4) if d_pct is not None else None),
                   "revenue_change_pct": (round(rev_pct, 4) if rev_pct is not None else None),
                   "gate_abs": round(gate),
                   "note": (f"{driver} +{d_pct:+.0%} while revenue {rev_pct:+.0%} over "
                            f"{months[i0]}..{months[i1]} -- build into softening demand")}
            out.append(rec)
            exc.append({"flag": f"working_capital_divergence_{driver}", "severity": "watch",
                        "detail": rec["note"]})
    return out, exc

# --------------------------------------------------------------------------- reclassification
def _stem(norm_label):
    s = _LEAD_ACCT.sub("", norm_label)
    s = _QUAL.sub("", s)
    s = _CLASS_TOKENS.sub("", s)
    s = re.sub(r"[-:]\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip()

def reclassifications(model, months, seg, li, orig, min_abs, tp, sheet="Balance Sheet"):
    rows = (getattr(model, "sheets", {}) or {}).get(sheet) or {}
    groups = {}
    for norm, vals in rows.items():
        if norm.startswith("total") or "&" in norm or "check" in norm:
            continue
        stem = _stem(norm)
        if len(stem) < 3:
            continue
        fv = [_f(v) for v in vals]
        if not any(v is not None and abs(v) >= float(min_abs) for v in fv):
            continue
        groups.setdefault(stem, []).append((norm, fv))
    idxs = [i for i in seg if i <= li]
    out, exc = [], []
    for stem, members in groups.items():
        if len(members) < 2:
            continue
        for t in idxs[1:]:
            deltas, base = [], 0.0
            for norm, vals in members:
                cur = vals[t] if (t < len(vals) and vals[t] is not None) else 0.0
                prev = vals[t - 1] if (0 <= t - 1 < len(vals) and vals[t - 1] is not None) else 0.0
                deltas.append((norm, cur - prev)); base += abs(prev)
            big = [(n, d) for n, d in deltas if abs(d) >= float(min_abs)]
            if len(big) < 2:
                continue
            signs = set(1 if d > 0 else -1 for _, d in big)
            gross = sum(abs(d) for _, d in big)
            net = sum(d for _, d in big)
            # scale gate: reclass must be LARGE vs the group's balances (skips routine amortization)
            mat = (gross >= max(2.0 * float(min_abs), tp["reclass_min_pct"] * base)) if base else (gross >= 2.0 * float(min_abs))
            if len(signs) >= 2 and gross > 0 and mat and abs(net) <= tp["reclass_net_frac"] * gross:
                out.append({"group": stem, "month": months[t],
                            "members": [{"line": orig.get(n, n), "move": round(d)} for n, d in big],
                            "group_net_move": round(net), "gross_move": round(gross),
                            "note": (f"Offsetting move within '{stem}' at {months[t]}: "
                                     f"net {net:+,.0f} on gross {gross:,.0f} -- reclassification, not a real change")})
                exc.append({"flag": "reclassification", "severity": "watch",
                            "detail": out[-1]["note"]})
    # de-dupe identical (group,month)
    seen, uo, ue = set(), [], []
    for r, e in zip(out, exc):
        k = (r["group"], r["month"])
        if k in seen:
            continue
        seen.add(k); uo.append(r); ue.append(e)
    return uo, ue

# --------------------------------------------------------------------------- emerging / interrupted line trends
def _leaf_series(model, sheet):
    rows = (getattr(model, "sheets", {}) or {}).get(sheet) or {}
    _SUB = {"net income", "gross profit", "net ordinary income", "net other income",
            "total - income", "total - cost of sales", "total - expense",
            "net change in cash for period", "total operating activities"}
    out = {}
    for norm, vals in rows.items():
        if norm.startswith("total") or norm.startswith("memo") or "&" in norm or "check" in norm:
            continue
        if norm in _SUB or norm.lstrip().startswith("memo"):
            continue
        fv = [_f(v) for v in vals]
        if sum(1 for v in fv if v is not None) >= 3:
            out[norm] = fv
    return out

def emerging_trends(model, months, seg, li, orig, min_abs, tp,
                    sheets=("Income Statement", "Balance Sheet")):
    """Per-leaf developing trends the rigid rules miss: a 3-5 month directional move that tolerates
    <=1 reversal and/or ended within run_end_tolerance months of latest (so a one-month blip or a
    recently-ended streak still surfaces). Additive; excludes clean runs ending at latest (those are
    already caught by monotonic_run)."""
    idxs = [i for i in seg if i <= li]
    tmin, tmax, tol = tp["trend_min_months"], tp["trend_max_months"], tp["run_end_tolerance"]
    if len(idxs) < tmin + 1:
        return [], []
    recs = []
    for sheet in sheets:
        for norm, fv in _leaf_series(model, sheet).items():
            mad = _mad([fv[i] for i in idxs])
            gate = max(float(min_abs), tp["k_vol"] * (mad or 0.0))
            best = None
            for end_off in range(0, tol + 1):
                epos = len(idxs) - 1 - end_off
                if epos < tmin:
                    break
                for w in range(min(tmax, epos), tmin - 1, -1):
                    seq = [fv[idxs[k]] for k in range(epos - w, epos + 1)]
                    if any(v is None for v in seq):
                        continue
                    cum = seq[-1] - seq[0]
                    if abs(cum) < gate:
                        continue
                    dr = 1 if cum > 0 else -1
                    deltas = [seq[k] - seq[k - 1] for k in range(1, len(seq))]
                    rev = sum(1 for d in deltas if d != 0 and (d > 0) != (dr > 0))
                    strict = (rev == 0 and end_off == 0)  # clean run to latest -> monotonic_run already has it
                    if rev <= 1 and not strict:
                        best = {"line": orig.get(norm, norm), "sheet": sheet,
                                "end_month": months[idxs[epos]], "window_months": w,
                                "cumulative_abs": round(cum),
                                "cumulative_pct": (round(cum / abs(seq[0]), 4) if seq[0] else None),
                                "direction": ("up" if dr > 0 else "down"), "reversals": rev,
                                "gate_abs": round(gate),
                                "kind": ("emerging_trend" if end_off == 0 else "recent_run")}
                        break
                if best:
                    break
            if best:
                recs.append(best)
    recs.sort(key=lambda r: abs(r["cumulative_abs"]) / (r["gate_abs"] or 1), reverse=True)
    recs = recs[:18]
    exc = [{"flag": "emerging_trend", "severity": "watch",
            "detail": (f"{r['line']} {r['direction']} {r['window_months']}mo to {r['end_month']} "
                       f"({r['cumulative_abs']:+,}); {r['reversals']} reversal(s); kind={r['kind']}")}
           for r in recs]
    return recs, exc


# --------------------------------------------------------------------------- entry
def build(model, S, months, li, cur_seg_idxs, cfg, orig, min_abs):
    tp = _tp(cfg)
    seg = list(cur_seg_idxs)
    rt, rt_exc = ratio_trends(S, months, seg, li, tp)
    wc, wc_exc = working_capital_divergence(S, months, seg, li, min_abs, tp)
    rc, rc_exc = reclassifications(model, months, seg, li, orig or {}, min_abs, tp)
    et, et_exc = emerging_trends(model, months, seg, li, orig or {}, min_abs, tp)
    return {
        "ratio_trends": rt or None,
        "working_capital_divergence": wc or None,
        "reclassifications": rc or None,
        "emerging_trends": et or None,
        "exceptions": rt_exc + wc_exc + rc_exc + et_exc,
        "params": {"dollar_gate": round(float(min_abs)), **tp,
                   "note": "scale-aware: gate = max(scale dollar floor, k_vol x series MAD); % values are fallbacks"},
    }
