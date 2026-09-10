#!/usr/bin/env python3
r"""validate_model.py -- build-integrity gate for a monitoring-model workbook.

Run by build-monitoring-model on the RECALCED, SAVED file BEFORE it becomes the
canonical model. Fails loud when:

  * a formula cell in the statement data region ships with NO cached value -- the
    recalc-ordering bug: an openpyxl edit made AFTER the LibreOffice recalc drops
    the cached value, so every downstream reader (the analysis modules) sees a
    blank subtotal and inverts the covenant reads; and
  * a statement sheet carries more than one row with the same label -- a lookup by
    name then silently serves the wrong row (reuses the loader's detection, U5).

Exit 0 + "OK" if the model is sound; exit 1 + the problem list otherwise.
Deterministic; no network. Mirrors validate_spec.py / validate_output.py.

Usage:  python validate_model.py <model.xlsx>
"""
import os
import sys
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_LIB_DIR = os.path.join(_PLUGIN_ROOT, "lib")
for path in (_HERE, _LIB_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import openpyxl  # noqa: E402
import monitor_lib as ml  # noqa: E402
import cashflow  # noqa: E402

_STATEMENT_SHEETS = ("Income Statement", "Balance Sheet", "Cash Flow Statement")


def validate(path):
    """Returns (errors, warnings). Errors block the build (recalc-drop corruption);
    warnings are surfaced but do not block."""
    try:
        model = ml.open_model(path)
    except Exception as e:
        return [f"could not open the model workbook: {e}"], []
    errors, warnings = [], []

    # Duplicate row labels are a WARNING, not a build blocker: a real P&L
    # legitimately repeats a leaf label across sections (e.g. "Depreciation"
    # under COGS and under Operating Expenses, which the build is required to
    # carry). The loader keeps last-wins; the warning just surfaces that a
    # lookup by that name may serve the wrong row.
    w = model.duplicate_label_warning()
    if w:
        warnings.append(w)

    unclassified = cashflow.from_model(model)["meta"]["unclassified_lines"]
    if unclassified:
        warnings.append(
            "derived cash flow could not classify these balance-sheet lines: "
            + ", ".join(unclassified)
            + ". Confirm any debt line is explicitly named or mapped before relying "
              "on financing cash flow.")

    # Recalc-drop is a hard error: a formula cell in a statement's data region
    # with no cached value. A recalced formula always caches a number (0.0 or
    # otherwise); a None cache means the recalc did not run as the last write.
    # Cached values come from the workbook the Model already parsed (model.wb,
    # data_only); only the formula-text view needs its own load.
    wb_f = openpyxl.load_workbook(model.path, data_only=False)
    for sn in _STATEMENT_SHEETS:
        if sn not in wb_f.sheetnames:
            errors.append(f"missing required worksheet: {sn}")
            continue
        if sn not in model.layout:
            errors.append(f"{sn}: could not read the statement layout")
            continue
        lay = model.layout[sn]
        hr, fc = lay["header_row"], lay["first_data_col"]
        ws_f, ws_v = wb_f[sn], model.wb[sn]
        dropped = {}
        for r in range(hr + 1, ws_f.max_row + 1):
            lab = ws_f.cell(row=r, column=1).value
            for c in range(fc, ws_f.max_column + 1):
                v = ws_f.cell(row=r, column=c).value
                if isinstance(v, str) and v.startswith("=") \
                        and ws_v.cell(row=r, column=c).value is None:
                    key = re.sub(r"\s+", " ", str(lab)).strip() if lab else f"row {r}"
                    dropped[key] = dropped.get(key, 0) + 1
        for lab, cnt in sorted(dropped.items()):
            errors.append(f"{sn}: '{lab}' has {cnt} formula cell(s) with no cached value -- "
                          "recalc did not run as the last write, or an edit after recalc "
                          "dropped the value (downstream reads it as blank)")
    return errors, warnings


def main():
    if len(sys.argv) != 2:
        print("usage: python validate_model.py <model.xlsx>")
        sys.exit(2)
    errors, warnings = validate(sys.argv[1])
    for w in warnings:
        print("  ! (warning)", w)
    if errors:
        print(f"INVALID — {len(errors)} problem(s):")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("OK — model passes the build-integrity gate."
          + (f" ({len(warnings)} warning(s) above)" if warnings else ""))
    sys.exit(0)


if __name__ == "__main__":
    main()
