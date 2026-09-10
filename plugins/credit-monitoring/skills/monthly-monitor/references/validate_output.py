"""validate_output.py — assert a module output JSON conforms to the provenance contract.

The write-time gate for trace-to-file and calculation records:
a figure without a source ref and a calc note is a SCHEMA FAILURE, not a style
choice — the same way validate_spec.py gates the covenant spec. Run it on every
module output before archiving; a failure names the offending figure.

What it enforces
  * envelope: module (str), period (ISO "YYYY-MM"), results (list),
    exceptions (list), source_files (list of path strings)
  * run stamp: run_id (str) + produced_at (ISO UTC) — what says WHICH run
    produced this and orders two answers for the same month. A skip record
    carries them too; a run of five modules shares one run_id
  * `basis` is a LABEL, short enough to group figures by; prose about how the
    period was prepared belongs in `methodology`
  * `reporting_unit` — an output that publishes any figure says what those
    figures are denominated in: scale (dollars per model unit: 1 whole dollars,
    1000 a model in thousands) + currency. A model in thousands and a
    model in dollars produce identical-looking JSON, so nothing downstream can
    tell them apart, and a dollar-denominated threshold compared against either
    is right in one case and wrong by 1000x in the other. Records with no
    figures at all (a skip, a coverage record) need none
  * source_files entries name a SHARED FOLDER, not a machine: a sandbox mount
    (/sessions/*/mnt/*), a drive letter, or somebody's home directory fails.
    A reader on another machine has to be able to open what a figure cites,
    and those three forms are true only where the run happened
    (engines: monitor_lib.SourceIndex / unresolve_path)
  * skip records ({"skipped": true}) need a reason; period may then be null
  * figure rule: every dict under `results` that carries a numeric value must
    be COVERED — itself or an enclosing record carries BOTH a valid
    `source_ref` and a non-empty `calc` note. Section-level coverage is
    deliberate: a parent record's ref covers its sub-figures, which is how
    outputs stay compact (refs on memo-surfaceable records, not every cell).
  * a module whose figures live OUTSIDE `results` (e.g. three-statement's
    line_read / mom_bridge / liquidity) declares those top-level keys in
    `figure_collections`: each named subtree is walked with the same figure
    rule. Undeclared meta (narrative, raw series dumps) is not walked.
  * every source_ref resolves: {"file": i} indexes into source_files; the
    optional {sheet,row,cells,period,label,section,...} detail is free-form
  * plausibility: a `pct_of_total` is a SHARE of a stated total, so a value
    outside +/-`PCT_OF_TOTAL_LIMIT` fails — a counterparty exceeding the grand
    total means the numerator and denominator came off different row
    sets (a group parent counted alongside its own members), and the figure
    would otherwise reach the memo looking like a real share. `ties`/
    `reconciles`-style booleans stay the engine's own call; this rule catches
    only arithmetic that cannot be a share.

Exit 0 + "OK" if valid; exit 1 + the list of problems otherwise.
Deterministic; stdlib only; no network.

Usage:  python validate_output.py [--period YYYY-MM] <module-output.json> [more.json ...]
        (--period: the run's current_period; every stamped output must match it —
        the cross-module half of the one-period-stamp-per-run rule)
"""
import json
import re
import sys

PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
# Any absolute filesystem path is true only on the machine that wrote it.
# Portable entries are logical `source:` / `store:` paths or relative names.
MACHINE_PATH = re.compile(r"^(?:[\\/]|[A-Za-z]:[\\/])")
# ISO 8601 with an offset or 'Z' — monitor_lib.run_stamp writes UTC seconds.
PRODUCED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
                         r"(\.\d+)?(Z|[+-]\d{2}:?\d{2})$")

# A share of a total, expressed as a fraction. The limit is deliberately loose:
# one member CAN exceed the whole when another carries a credit balance, so only
# a figure no set of shares could produce fails.
PCT_OF_TOTAL_LIMIT = 1.5

# A `basis` is a label, not a paragraph. The longest real one in the shipped
# engines is "auto-aggregated funded debt (ex-capital-leases)" at 46 characters;
# the essays this catches ran 300 to 545.
BASIS_LABEL_MAX = 80
MAX_BASIS_DEPTH = 12


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _name_of(d):
    """Best human name for a figure record, for loud error messages."""
    for k in ("id", "name", "label", "line", "metric", "covenant", "entity", "test"):
        v = d.get(k)
        if isinstance(v, str) and v:
            return f" ({k}={v!r})"
    return ""


def _check_basis(node, errs, where, depth=0):
    """`basis` labels a figure — actual, budget, organic, spot — so it stays short
    enough to group by.

    It was doing two jobs: a one-word label on a figure, and a 545-character
    paragraph on the envelope describing how the borrower's whole month was
    prepared. Anything grouping figures by basis then got a mix of labels and
    essays. The prose belongs in `methodology`; this keeps it from drifting back.
    """
    if depth > MAX_BASIS_DEPTH:
        return
    if isinstance(node, dict):
        v = node.get("basis")
        if isinstance(v, str) and len(v) > BASIS_LABEL_MAX:
            errs.append(
                f"{where}.basis is {len(v)} characters — 'basis' labels a figure "
                f"(actual / budget / organic / spot), so it stays under "
                f"{BASIS_LABEL_MAX}. Prose about how the period was prepared goes "
                f"in 'methodology': {v[:60]!r}...")
        for k, child in node.items():
            if isinstance(child, (dict, list)):
                _check_basis(child, errs, f"{where}.{k}", depth + 1)
    elif isinstance(node, list):
        for i, child in enumerate(node):
            if isinstance(child, (dict, list)):
                _check_basis(child, errs, f"{where}[{i}]", depth + 1)


def _any_number(node, depth=0):
    """True if this subtree publishes a figure. A `source_ref` holds row and page
    numbers, not figures, so it does not count."""
    if depth > 24:
        return False
    if isinstance(node, dict):
        return any(_is_number(v) and k != "file" for k, v in node.items()) or any(
            _any_number(v, depth + 1) for k, v in node.items() if k != "source_ref")
    if isinstance(node, list):
        return any(_any_number(v, depth + 1) for v in node)
    return False


def _check_reporting_unit(out, errs, publishes_figures):
    """An output that publishes figures says what those figures are denominated
    in.

    A model in thousands and a model in whole dollars produce identical-looking
    JSON, so a reader — the memo, a portfolio answer, a person — has no way to
    know which it has. Undeclared, a $1,000,000 covenant floor gets compared
    against model cash of 2,200 and publishes a breach. The unit rides on the
    envelope rather than on each figure: one declaration per run, from
    borrower-config, via monitor_lib.conclusion(unit=...).

    A record that publishes no figure (a skip, a coverage record) needs none.
    """
    unit = out.get("reporting_unit")
    if unit is None:
        if publishes_figures:
            errs.append(
                "missing 'reporting_unit' — an output carrying figures must say "
                "what they are denominated in: pass unit=monitor_lib."
                "reporting_unit(cfg) to monitor_lib.conclusion, and declare "
                "model.scale + model.currency in borrower-config")
        return
    if not isinstance(unit, dict):
        errs.append(f"'reporting_unit' must be an object with scale + currency "
                    f"(got {type(unit).__name__})")
        return
    scale = unit.get("scale")
    if not (_is_number(scale) and scale > 0):
        errs.append(f"reporting_unit.scale must be the number of dollars one "
                    f"model unit stands for — 1 for whole dollars, 1000 for a "
                    f"model in thousands (got {scale!r})")
    if not (isinstance(unit.get("currency"), str) and unit["currency"].strip()):
        errs.append(f"reporting_unit.currency must name the ledger's currency, "
                    f"e.g. 'USD' (got {unit.get('currency')!r})")


def _check_source_ref(ref, n_files, errs, where):
    """A source_ref is one ref dict or a list of them; each needs a resolvable
    file index. Detail fields (sheet/row/cells/period/label/section/...) are
    free-form — the ref's JOB is to name the file and locate the figure in it."""
    refs = ref if isinstance(ref, list) else [ref]
    if not refs:
        errs.append(f"{where}: source_ref is empty")
        return False
    ok = True
    for j, r in enumerate(refs):
        w = f"{where}.source_ref[{j}]" if isinstance(ref, list) else f"{where}.source_ref"
        if not isinstance(r, dict):
            errs.append(f"{w}: must be an object with a 'file' index")
            ok = False
            continue
        f = r.get("file")
        if not (isinstance(f, int) and not isinstance(f, bool) and 0 <= f < n_files):
            errs.append(f"{w}: 'file' must index into source_files "
                        f"(got {f!r}, {n_files} file(s) indexed)")
            ok = False
        # An index is only an address while the file list is next to it. A ref
        # that names a store carries the path inside it too, or it addresses a
        # folder.
        if r.get("store") and not str(r.get("path_in_store") or "").strip():
            errs.append(f"{w}: names store {r['store']!r} with no "
                        "'path_in_store' — the path inside that shared folder "
                        "is what makes the ref openable")
            ok = False
        if not _check_place(r, errs, w):
            ok = False
        for u, up in enumerate(r.get("upstream") or []):
            if not isinstance(up, dict):
                errs.append(f"{w}.upstream[{u}]: must be an object naming the "
                            "borrower's own document")
                ok = False
            elif not _check_place(up, errs, f"{w}.upstream[{u}]"):
                ok = False
    return ok


def _check_place(r, errs, w):
    """A location inside a document is a number and a quote, or it is nothing. A page of 0 or a blank quote reads as traced when it is not."""
    ok = True
    for key in ("page", "line"):
        v = r.get(key)
        if v is None:
            continue
        if not (isinstance(v, int) and not isinstance(v, bool) and v >= 1):
            errs.append(f"{w}: {key} must be a 1-based number (got {v!r})")
            ok = False
    q = r.get("quote")
    if q is not None and not (isinstance(q, str) and q.strip()):
        errs.append(f"{w}: quote is present but empty — quote the wording the "
                    "figure was read from, or leave it out")
        ok = False
    return ok


def _covered(d, n_files, errs, where):
    """True if record `d` itself carries a valid source_ref + calc note."""
    has_ref = "source_ref" in d
    calc = d.get("calc")
    has_calc = isinstance(calc, str) and calc.strip() != ""
    if not (has_ref or has_calc):
        return False
    problems = []
    if not has_ref:
        problems.append("has a 'calc' note but no 'source_ref'")
    else:
        # a broken ref reports its own errors; still fall through so a
        # missing calc on the same record is reported in the SAME run
        _check_source_ref(d["source_ref"], n_files, errs, where)
    if not has_calc:
        problems.append("has a 'source_ref' but no 'calc' note (non-empty string)")
    for p in problems:
        errs.append(f"{where}{_name_of(d)}: {p}")
    return True  # partially covered records are reported above, once


def _walk(node, covered, n_files, errs, where):
    """Depth-first over `results`. A numeric-bearing dict must be covered by
    itself or an ancestor. source_ref contents are exempt (refs contain row
    numbers, not figures)."""
    if isinstance(node, dict):
        here = _covered(node, n_files, errs, where) or covered
        pct = node.get("pct_of_total")
        if _is_number(pct) and abs(pct) > PCT_OF_TOTAL_LIMIT:
            errs.append(
                f"{where}{_name_of(node)}: pct_of_total {pct!r} is not a share of "
                f"a total (limit +/-{PCT_OF_TOTAL_LIMIT}) — the numerator and the "
                "denominator came off different row sets; reconcile the parts "
                "against the total the engine divides by")
        uncovered_numbers = [k for k, v in node.items()
                             if _is_number(v) and k != "file"]
        if uncovered_numbers and not here:
            errs.append(
                f"{where}{_name_of(node)}: figure carries numeric value(s) "
                f"{uncovered_numbers} with no source_ref + calc on itself or "
                f"any enclosing record")
        for k, v in node.items():
            if k == "source_ref":
                continue
            _walk(v, here, n_files, errs, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, covered, n_files, errs, f"{where}[{i}]")


def validate(out):
    errs = []
    if not isinstance(out, dict):
        return ["output is not a JSON object"]

    if not (isinstance(out.get("module"), str) and out["module"]):
        errs.append("missing 'module' (non-empty string)")

    skipped = out.get("skipped") is True
    period = out.get("period")
    if skipped:
        reason = out.get("reason")
        if not (isinstance(reason, str) and reason.strip()):
            errs.append("skip record ({'skipped': true}) needs a non-empty 'reason'")
        if period is not None and not (isinstance(period, str) and PERIOD.match(period)):
            errs.append(f"period must be ISO 'YYYY-MM' or null on a skip record "
                        f"(got {period!r})")
    else:
        if not (isinstance(period, str) and PERIOD.match(period)):
            errs.append(f"period must be the ISO 'YYYY-MM' stamp (got {period!r}); "
                        "shape it with monitor_lib.conclusion / period_stamp")

    _check_basis(out, errs, "output")

    run_id = out.get("run_id")
    if not (isinstance(run_id, str) and run_id.strip()):
        errs.append("missing 'run_id' — the run that produced this output; "
                    "shape the envelope with monitor_lib.conclusion, and pass "
                    "the orchestrator's --run-id so one run's modules share it")
    produced_at = out.get("produced_at")
    if not (isinstance(produced_at, str) and PRODUCED_AT.match(produced_at)):
        errs.append(f"'produced_at' must be an ISO UTC timestamp "
                    f"(got {produced_at!r}) — it is what orders two answers "
                    "for the same month")

    files = out.get("source_files")
    if not isinstance(files, list) or any(
            not (isinstance(f, str) and f.strip()) for f in files or []):
        errs.append("missing 'source_files' (list of source path strings; may be "
                    "empty only when results carry no figures)")
        files = files if isinstance(files, list) else []
    for i, f in enumerate(files):
        if isinstance(f, str) and MACHINE_PATH.match(f.strip()):
            errs.append(
                f"source_files[{i}] names one machine ({f!r}) — record the file "
                "as \"<shared folder>:<path inside it>\" so a reader on another "
                "machine can open it and store hyperlinks can be joined "
                "downstream (engines: monitor_lib.SourceIndex; agents: pass the "
                "borrower-config path strings, not /sessions mounts)")

    results = out.get("results")
    if not isinstance(results, list):
        errs.append("missing 'results' (list)")
    else:
        _walk(results, False, len(files), errs, "results")

    collections = out.get("figure_collections", [])
    if not isinstance(collections, list) or any(
            not isinstance(k, str) for k in collections):
        errs.append("'figure_collections' must be a list of top-level key names")
        collections = []
    for key in collections:
        if key in ("results", "exceptions", "source_files", "figure_collections"):
            errs.append(f"figure_collections: {key!r} is envelope, not a meta collection")
            continue
        if key not in out:
            errs.append(f"figure_collections names {key!r} but the output has no such key")
            continue
        _walk(out[key], False, len(files), errs, key)

    if not isinstance(out.get("exceptions"), list):
        errs.append("missing 'exceptions' (list)")

    publishes = not skipped and (
        _any_number(results if isinstance(results, list) else [])
        or any(_any_number(out.get(k)) for k in collections))
    _check_reporting_unit(out, errs, publishes)
    return errs


def main():
    args = sys.argv[1:]
    run_period = None
    if args and args[0] == "--period":
        if len(args) < 2 or not PERIOD.match(args[1]):
            print("--period needs an ISO YYYY-MM value")
            sys.exit(2)
        run_period, args = args[1], args[2:]
    if not args:
        print("usage: python validate_output.py [--period YYYY-MM] "
              "<module-output.json> [more.json ...]")
        sys.exit(2)
    failed = False
    for path in args:
        with open(path, encoding="utf-8") as f:
            out = json.load(f)
        errs = validate(out)
        # One period stamp per run: with --period (the orchestrator passes the
        # run's current_period), any stamped output must match it. A skip
        # record with a null period is exempt (its period was underivable).
        if run_period and isinstance(out, dict):
            p = out.get("period")
            if p is not None and p != run_period:
                errs.append(f"period {p!r} does not match the run's period "
                            f"{run_period!r} (--period)")
        tag = out.get("module", "?") if isinstance(out, dict) else "?"
        if errs:
            failed = True
            print(f"INVALID {path} [{tag}] — {len(errs)} problem(s):")
            for e in errs:
                print("  -", e)
        else:
            n = len(out.get("results", []))
            print(f"OK {path} [{tag}] — period {out.get('period')}, "
                  f"{n} result record(s), every figure source-ref'd + calc-noted.")
            # How far the refs reach, said out loud: a figure whose source stops
            # at a folder, or lands in no shared store, is still a gap someone
            # has to close, and a silent count is how it stays open.
            # An action item is a thing a person must DO. It reached nobody while
            # it sat in an archived JSON, so the gate says it out loud and
            # orchestration.md requires the memo to carry it.
            items = out.get("action_items")
            items = [i for i in (items if isinstance(items, list) else []) if i]
            if items:
                print(f"   {len(items)} ACTION ITEM(S) — these belong in the memo, "
                      "not only in the note:")
                for i in items:
                    text = i if isinstance(i, str) else (
                        i.get("text") or i.get("item") or json.dumps(i))
                    print(f"     * {str(text).strip()[:160]}")

            reach = out.get("source_ref_reach")
            if isinstance(reach, dict) and any(reach.values()):
                short = reach.get("file", 0) + reach.get("unlocated", 0)
                print(f"   source refs — {reach.get('cell', 0)} reach a cell, "
                      f"{reach.get('file', 0)} stop at a file, "
                      f"{reach.get('unlocated', 0)} land in no configured store"
                      + (f"; {short} cannot be opened to a number" if short else ""))
                total = sum(reach.get(k, 0) for k in ("cell", "file", "unlocated"))
                doc = reach.get("borrower_doc", 0)
                if total:
                    print(f"   {doc} of {total} reach the borrower's OWN document; "
                          f"{total - doc} name only a file we produced")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
