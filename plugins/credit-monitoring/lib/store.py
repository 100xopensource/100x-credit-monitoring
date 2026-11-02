#!/usr/bin/env python3
"""Prepare the user-selected root for monitoring outputs."""

import argparse
import json
import os
from pathlib import Path
import re
import sys

try:
    from plugin.lib.persistence import *  # noqa: F401,F403 — checkout/tests
except ModuleNotFoundError:
    from persistence import *  # noqa: F401,F403 — extracted plugin script

STORE_NAME = "credit-monitoring-output"
REGISTRY_NAME = "credit-monitoring-registry.json"
PUBLISHED_NAME = "published.json"
LEDGER_NAME = "run-ledger.json"
BORROWERS_DIR = "borrowers"
ARTIFACTS_DIR = "artifacts"
PAGES_DIR = "pages"
SCHEMA_NAME = "store-schema.json"
STORE_SCHEMA_VERSION = 1
STORE_SCHEMA = "credit-monitoring-store/v1"
PUBLISHED_SCHEMA = "credit-monitoring-published/v1"
MANIFEST_SCHEMA = "credit-monitoring-run-manifest/v1"
LEDGER_SCHEMA = "credit-monitoring-run-ledger/v1"
PORTFOLIO_DIR = "portfolio"
ARTIFACT_DIR = "Artifact"
HOSTED_ARTIFACT_NAME = "hosted-artifact.json"
_DS_STORE_NAME = ".DS_Store"
# Mirrors refresh-dashboard's build_artifact.py foreign_names(): the only names
# that skill (and portfolio-artifact) ever write into an Artifact folder.
_ARTIFACT_KEPT_NAMES = {"index.json", "fonts.html", ".dashboard", "views", "desktop.ini", "thumbs.db"}
_ARTIFACT_PAGE_SUFFIXES = ("--deal.json", "--memo.html", "--workbook.json")
_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_RUN = re.compile(r"^run-\d{8}-\d{6}$")
# Inert directories are tolerated, never read or restored by delivery.
_RETIRING_CURRENT = re.compile(r"^current\.retiring-\d+$")
# Two backup-naming conventions observed alongside hosted-artifact.json; no
# current plugin code writes them, but they are inert historical copies of
# the same JSON shape as the primary file.
_HOSTED_ARTIFACT_BACKUP = re.compile(
    r"^hosted-artifact\.json\.bak-[a-z0-9]+-[0-9a-f]{8}$"
    r"|^hosted-artifact\.prior-[0-9a-f]{8}(?:-[a-z0-9]+)?\.json$"
)



class StoreError(ValueError):
    """The selected parent folder cannot safely hold a monitoring store."""


# Why the check refuses a path, in the words store-setup passes to the analyst.
_LINK = "is a symbolic link"
_UNKNOWN = "is not something the plugin keeps here"
_DAMAGED = "is damaged or not in the format the plugin writes"
_NOT_MONTH = "is not named for a month (YYYY-MM)"
_NOT_RUN = "is not named for a run (run-YYYYMMDD-HHMMSS)"
# A refusal names this many paths, then counts the rest.
_NAMED_AT_MOST = 10


def _contains(parent, child):
    parent, child = Path(parent).resolve(), Path(child).resolve()
    return parent == child or parent in child.parents


def _plain(path, kind):
    return not path.is_symlink() and getattr(path, f"is_{kind}")()


def _wrong_kind(path, kind):
    """Why `path` is not a plain folder (`kind` "dir") or file, else None."""
    if path.is_symlink():
        return _LINK
    if not getattr(path, f"is_{kind}")():
        return "is not a folder" if kind == "dir" else "is not a file"
    return None


def _bad_file(path, readable):
    """Why `path` is not a plain file that `readable` accepts, else None."""
    return _wrong_kind(path, "file") or (None if readable(path) else _DAMAGED)


def _present(path):
    """True if something occupies `path`, including a dangling symlink.

    `Path.exists()` follows symlinks and reports False for a dangling one,
    which would let a broken symlink slip through an `if slot.exists(): ...`
    gate as if the slot were simply empty. Callers that need "is this slot
    occupied at all" (before handing it to a `_wrong_kind()`-based check that
    will correctly refuse the symlink) should use this instead.
    """
    return path.is_symlink() or path.exists()


def _links_in(folder):
    # rglob lists a symlink but never descends into one.
    for item in folder.rglob("*"):
        if item.is_symlink():
            yield item, _LINK


def _benign_ds_store(item):
    return item.name == _DS_STORE_NAME and _plain(item, "file")


_BENIGN_VIEW_NAMES = {_DS_STORE_NAME.lower(), "desktop.ini", "thumbs.db"}


def _benign_view_metadata(item):
    """The Artifact root's allowance for OS metadata, inside `views/` too.
    A symlink is not a plain file, so it is still refused."""
    return item.name.lower() in _BENIGN_VIEW_NAMES and _plain(item, "file")


def _json_dict(path):
    try:
        return isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
    except (OSError, ValueError):
        return False


def _json_lines(path):
    try:
        return all(
            isinstance(json.loads(line), dict)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except (OSError, ValueError):
        return False


# Each `_production_*` check yields (path, why) for every path it refuses.
# Symbolic links inside a borrower folder are found by `_production_borrower`.
def _production_runs(runs):
    if why := _wrong_kind(runs, "dir"):
        yield runs, why
        return
    for period in runs.iterdir():
        if _benign_ds_store(period):
            continue
        if why := _wrong_kind(period, "dir") or (
                None if _PERIOD.fullmatch(period.name) else _NOT_MONTH):
            yield period, why
            continue
        for run in period.iterdir():
            if _benign_ds_store(run):
                continue
            if why := _wrong_kind(run, "dir") or (
                    None if _RUN.fullmatch(run.name) else _NOT_RUN):
                yield run, why
            elif (run / MANIFEST_NAME).exists() and read_manifest(run) is None:
                yield run / MANIFEST_NAME, _DAMAGED


# The files a borrower's `.record/` may hold, each with what makes it readable.
_RECORD_FILES = {
    MANIFEST_NAME: lambda path: read_manifest(path.parent) is not None,
    CHANGELOG_NAME: _json_lines,
    PUBLISHED_NAME: _json_dict,
    LEDGER_NAME: _json_dict,
    # Present between edit intake and finish, and left behind for good where
    # the store's host can write but not delete files. run_lib ignores an
    # acceptance bound to a superseded run or already consumed by a frozen run.
    EDIT_INTAKE_NAME: _json_dict,
    LOCK_NAME: lambda path: True,
}


def _production_record(record):
    if why := _wrong_kind(record, "dir"):
        yield record, why
        return
    current = record / CURRENT_DIR
    for item in record.iterdir():
        if (item.name in _RECORD_FILES or item.name in (CURRENT_DIR, RUNS_DIR, PAGES_DIR)
                or _benign_ds_store(item)):
            continue
        if not _RETIRING_CURRENT.fullmatch(item.name):
            yield item, _UNKNOWN
        # Only tolerated once the swap it came from has finished (`current`
        # is present); a retiring dir with no `current` means the crash hit
        # mid-swap and the store needs a person, not a silent guess here.
        elif not current.exists():
            yield item, "is left from an interrupted update, with no current folder beside it"
        elif why := _wrong_kind(item, "dir"):
            yield item, why
    for folder in (current, record / PAGES_DIR):
        if _present(folder) and (why := _wrong_kind(folder, "dir")):
            yield folder, why
    runs = record / RUNS_DIR
    if _present(runs):
        yield from _production_runs(runs)
    for name, readable in _RECORD_FILES.items():
        path = record / name
        if _present(path) and (why := _bad_file(path, readable)):
            yield path, why


def _production_borrower(borrower):
    """`borrowers/<key>/`. The plugin owns `.record/` and `_model/` and checks
    them. Anything else is the analyst's own, which the plugin never reads or
    writes, so the check ignores it unless a symbolic link or a second
    `.record` hides in it."""
    if why := _wrong_kind(borrower, "dir"):
        yield borrower, why
        return
    for item in borrower.rglob("*"):
        if item.is_symlink():
            yield item, _LINK
        elif item.name == RECORD_DIR and item.parent != borrower and item.is_dir():
            yield item, (f"is a .record folder in the wrong place: only "
                         f"{BORROWERS_DIR}/{borrower.name}/{RECORD_DIR} holds this borrower's records")
    record = borrower / RECORD_DIR
    if _present(record):
        yield from _production_record(record)
    else:
        yield borrower, f"is not a borrower folder (it has no {RECORD_DIR} folder)"
    model = borrower / "_model"
    if _present(model) and (why := _wrong_kind(model, "dir")):
        yield model, why


def _artifact_page_name(name):
    if name.lower() in _ARTIFACT_KEPT_NAMES:
        return True
    if name.startswith("narrative--") and name.endswith(".json"):
        return True
    return any(name.endswith(suffix) for suffix in _ARTIFACT_PAGE_SUFFIXES)


def _production_views(views):
    """`views/<borrower>/<run id>/`: one folder per run, holding only its pages."""
    if why := _wrong_kind(views, "dir"):
        yield views, why
        return
    for borrower in views.iterdir():
        if _benign_view_metadata(borrower):
            continue
        if why := _wrong_kind(borrower, "dir"):
            yield borrower, why
            continue
        for run in borrower.iterdir():
            if _benign_view_metadata(run):
                continue
            if why := _wrong_kind(run, "dir") or (
                    None if _RUN.fullmatch(run.name) else _NOT_RUN):
                yield run, why
                continue
            for page in run.iterdir():
                if _benign_view_metadata(page):
                    continue
                if why := _wrong_kind(page, "file") or (
                        None if page.name.endswith(_ARTIFACT_PAGE_SUFFIXES)
                        else "is not a dashboard page"):
                    yield page, why


def _production_artifact_folder(folder):
    if why := _wrong_kind(folder, "dir"):
        yield folder, why
        return
    for item in folder.iterdir():
        if _benign_ds_store(item):
            continue
        name = item.name.lower()
        if not _artifact_page_name(item.name):
            yield item, "is not a dashboard file"
        elif name == ".dashboard":
            if why := _wrong_kind(item, "dir"):
                yield item, why
            else:
                yield from _links_in(item)
        elif name == "views":
            yield from _production_views(item)
        elif why := _bad_file(item, _json_dict if name == "index.json" else lambda path: True):
            yield item, why


def _production_portfolio(portfolio):
    if why := _wrong_kind(portfolio, "dir"):
        yield portfolio, why
        return
    hosted = portfolio / HOSTED_ARTIFACT_NAME
    for item in portfolio.iterdir():
        if item.name in (ARTIFACT_DIR, HOSTED_ARTIFACT_NAME) or _benign_ds_store(item):
            continue
        # Backups of a prior hosted-artifact.json, in the two naming shapes
        # seen in the wild. No current writer produces them; only tolerated
        # alongside a live primary file, and only if they carry the same
        # inert JSON-dict shape as that primary — never promoted or read.
        if not _HOSTED_ARTIFACT_BACKUP.fullmatch(item.name):
            yield item, _UNKNOWN
        elif not hosted.exists():
            yield item, f"is a backup with no {HOSTED_ARTIFACT_NAME} beside it"
        elif why := _bad_file(item, _json_dict):
            yield item, why
    artifact = portfolio / ARTIFACT_DIR
    if _present(artifact):
        yield from _production_artifact_folder(artifact)
    if _present(hosted) and (why := _bad_file(hosted, _json_dict)):
        yield hosted, why


def _production_artifacts(artifacts):
    if why := _wrong_kind(artifacts, "dir"):
        yield artifacts, why
        return
    for item in artifacts.iterdir():
        if item.name != PORTFOLIO_DIR and not _benign_ds_store(item):
            yield item, _UNKNOWN
    portfolio = artifacts / PORTFOLIO_DIR
    if _present(portfolio):
        yield from _production_portfolio(portfolio)


def _production_store(root):
    allowed = {REGISTRY_NAME, BORROWERS_DIR, ARTIFACTS_DIR, SCHEMA_NAME}
    for item in root.iterdir():
        if item.name not in allowed and not _benign_ds_store(item):
            yield item, _UNKNOWN
    registry = root / REGISTRY_NAME
    if _present(registry) and (why := _bad_file(registry, _json_dict)):
        yield registry, why
    artifacts = root / ARTIFACTS_DIR
    if _present(artifacts):
        yield from _production_artifacts(artifacts)
    borrowers = root / BORROWERS_DIR
    if not _present(borrowers):
        return
    if why := _wrong_kind(borrowers, "dir"):
        yield borrowers, why
        return
    for item in borrowers.iterdir():
        if not _benign_ds_store(item):
            yield from _production_borrower(item)


def _refusal(root, problems):
    """The analyst-facing refusal: each refused path inside the store, and why."""
    named = {}
    for path, why in problems:
        named.setdefault(path.relative_to(root).as_posix(), why)
    lines = [f"- {path} {why}" for path, why in sorted(named.items())]
    if len(lines) > _NAMED_AT_MOST:
        lines[_NAMED_AT_MOST:] = [f"- and {len(lines) - _NAMED_AT_MOST} more"]
    return (f"{STORE_NAME} is not a monitoring store this plugin can use, "
            "so it was left unchanged:\n" + "\n".join(lines))


def prepare_store_root(selected_parent, source_root=None):
    """Create or safely reuse `<selected_parent>/credit-monitoring-output`.

    This is the public adapter's storage operation. Public users choose its
    parent through Cowork.
    """
    parent = Path(selected_parent).expanduser()
    if not parent.is_dir():
        raise StoreError("The selected output parent folder is not a folder.")
    parent = parent.resolve()
    if not os.access(parent, os.W_OK):
        raise StoreError("The selected output parent folder is not writable.")
    root = parent / STORE_NAME
    if root.is_symlink():
        raise StoreError(f"{STORE_NAME} must not be a symbolic link.")
    if source_root is not None:
        source = Path(source_root).expanduser().resolve()
        resolved_root = root.resolve()
        if _contains(resolved_root, source) or _contains(source, resolved_root):
            raise StoreError("The source and output folders must be separate.")

    try:
        root.mkdir()
    except FileExistsError:
        if not root.is_dir():
            raise StoreError(f"{STORE_NAME} exists but is not a folder.")
    if any(root.iterdir()):
        if (root / PUBLISHED_NAME).exists():
            raise StoreError(
                f"{STORE_NAME} has an unsupported old store layout with a root published pointer "
                f"({PUBLISHED_NAME}). "
                "Only new stores with per-borrower accepted records are supported."
            )
        flat = sorted(item.name for item in root.iterdir()
                      if item.name != BORROWERS_DIR and item.is_dir() and (item / RECORD_DIR).is_dir())
        if flat:
            raise StoreError(
                f"{STORE_NAME} has an unsupported old store layout with flat borrower folders at root "
                f"({', '.join(flat)}). Only new stores under borrowers/ are supported."
            )
        marker = root / SCHEMA_NAME
        try:
            version = json.loads(marker.read_text(encoding="utf-8")) if _plain(marker, "file") else None
        except (OSError, ValueError):
            version = None
        if version != {"schema": STORE_SCHEMA, "schema_version": STORE_SCHEMA_VERSION}:
            raise StoreError(
                f"Unsupported or unversioned store: {SCHEMA_NAME} is missing or names another store "
                "version. Create a new store; this folder was not changed."
            )
        problems = list(_production_store(root))
        if problems:
            raise StoreError(_refusal(root, problems))
    else:
        write_output({"schema": STORE_SCHEMA, "schema_version": STORE_SCHEMA_VERSION},
                     root / SCHEMA_NAME)
    return root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepare", choices=("prepare",))
    parser.add_argument("--parent", required=True)
    parser.add_argument("--source-root")
    args = parser.parse_args(argv)
    try:
        print(json.dumps({
            "store_root": str(prepare_store_root(args.parent, args.source_root))
        }))
        return 0
    except (OSError, StoreError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
