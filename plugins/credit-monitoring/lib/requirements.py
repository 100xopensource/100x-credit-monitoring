#!/usr/bin/env python3
"""Read-only runtime prerequisite check. No provisioning, network or output writes."""
import argparse
import importlib
import importlib.util
import platform
import re
import sys

WORKBOOK_REQUIREMENT = "openpyxl==3.1.5"


def import_dependency(name):
    """Only dependency import failures degrade; interrupts and exits still propagate."""
    try:
        return importlib.import_module(name), None
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        return None, f"import failed ({type(exc).__name__})"


def _workbook_support():
    module, problem = import_dependency("openpyxl")
    if problem:
        return None, None, problem
    version = getattr(module, "__version__", "")
    # Compare numeric release components, not lexical order (3.10 is above 3.1).
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+)*", version):
        return None, None, "version unverifiable"
    release = tuple(int(part) for part in version.split("."))
    if release != (3, 1, 5):
        return None, None, f"version {version} outside declared support"
    utils, problem = import_dependency("openpyxl.utils")
    if problem or not callable(getattr(utils, "get_column_letter", None)):
        return None, None, problem or "workbook utilities unavailable"
    return module, utils.get_column_letter, None


openpyxl, get_column_letter, workbook_problem = _workbook_support()


def workbook_diagnostic(capability="workbook build/read/validate and analysis"):
    return (f"Required {WORKBOOK_REQUIREMENT} unavailable: {workbook_problem}. "
            f"Affected capability: {capability}. "
            f"Interpreter: {sys.executable} (Python {platform.python_version()}). "
            "Explicitly provision the declared dependency in this interpreter using an "
            "approved environment/package source, then retry. Existing results are not replaced "
            "by this prerequisite check.")


class WorkbookPrerequisiteError(RuntimeError):
    """Environment failure, never a missing-financial-input/skip condition."""


def check_workbook():
    """Library backstop; CLI checks must precede broad input-error/skip handlers."""
    if workbook_problem:
        raise WorkbookPrerequisiteError(workbook_diagnostic())


def require_workbook():
    """Actionable CLI failure, before input handling, assembly or output writes."""
    if workbook_problem:
        print(workbook_diagnostic(), file=sys.stderr)
        raise SystemExit(1)


def recalculation_status():
    """Use only the owner's read-only resolver, never its recalculation operation."""
    name = f"{__package__}.source_register" if __package__ else "source_register"
    if importlib.util.find_spec(name) is not None:
        owner = importlib.import_module(name)
        discovery = getattr(owner, "find_soffice", None)
        if callable(discovery):
            state = "ready" if discovery() else "unavailable"
            return f"{state} — owner executable discovery only; recalculation/fallback not verified"
    return "integration pending — owner read-only discovery API not integrated; recalculation/fallback not verified"


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    print(f"Interpreter: {sys.executable}; Python {platform.python_version()}; platform: {sys.platform}")
    if workbook_problem:
        print("required: unavailable — " + workbook_diagnostic())
    else:
        print(f"required: ready — {WORKBOOK_REQUIREMENT} (actual {openpyxl.__version__}); workbook support")
    pdf, problem = import_dependency("pypdf")
    print("optional: PDF reader " + ("unavailable — PDF evidence remains unresolved"
                                     if problem else "ready — importable pypdf; extraction not verified"))
    print("optional: recalculation " + recalculation_status())
    return 1 if workbook_problem else 0


if __name__ == "__main__":
    sys.exit(main())
