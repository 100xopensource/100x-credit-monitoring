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
_ARTIFACT_KEPT_NAMES = {"index.json", "fonts.html", ".dashboard", "desktop.ini", "thumbs.db"}
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


def _contains(parent, child):
    parent, child = Path(parent).resolve(), Path(child).resolve()
    return parent == child or parent in child.parents


def _plain(path, kind):
    return not path.is_symlink() and getattr(path, f"is_{kind}")()


def _present(path):
    """True if something occupies `path`, including a dangling symlink.

    `Path.exists()` follows symlinks and reports False for a dangling one,
    which would let a broken symlink slip through an `if slot.exists(): ...`
    gate as if the slot were simply empty. Callers that need "is this slot
    occupied at all" (before handing it to a `_plain()`-based validator that
    will correctly reject the symlink) should use this instead.
    """
    return path.is_symlink() or path.exists()


def _plain_tree(root):
    return all(not item.is_symlink() for item in root.rglob("*"))


def _benign_ds_store(item):
    return item.name == _DS_STORE_NAME and _plain(item, "file")


def _json_dict(path):
    try:
        return isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
    except (OSError, ValueError):
        return False


def _json_lines(path):
    if not _plain(path, "file"):
        return False
    try:
        return all(
            isinstance(json.loads(line), dict)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except (OSError, ValueError):
        return False


def _production_runs(runs):
    if not _plain(runs, "dir"):
        return False
    for period in runs.iterdir():
        if _benign_ds_store(period):
            continue
        if not _plain(period, "dir") or not _PERIOD.fullmatch(period.name):
            return False
        for run in period.iterdir():
            if _benign_ds_store(run):
                continue
            if not _plain(run, "dir") or not _RUN.fullmatch(run.name):
                return False
            if not _plain_tree(run):
                return False
            if (run / MANIFEST_NAME).exists() and read_manifest(run) is None:
                return False
    return True


def _production_current(current):
    if not _plain(current, "dir") or not _plain_tree(current):
        return False
    return True


def _production_record(record):
    if not _plain(record, "dir") or not _plain_tree(record):
        return False
    current = record / CURRENT_DIR
    allowed = {CURRENT_DIR, RUNS_DIR, MANIFEST_NAME, CHANGELOG_NAME,
               PUBLISHED_NAME, LEDGER_NAME, PAGES_DIR, LOCK_NAME}
    for item in record.iterdir():
        if item.name in allowed or _benign_ds_store(item):
            continue
        # Only tolerated once the swap it came from has finished (`current`
        # is present); a retiring dir with no `current` means the crash hit
        # mid-swap and the store needs a person, not a silent guess here.
        if (
            current.exists()
            and _RETIRING_CURRENT.fullmatch(item.name)
            and _production_current(item)
        ):
            continue
        return False
    if _present(current) and not _production_current(current):
        return False
    runs = record / RUNS_DIR
    if _present(runs) and not _production_runs(runs):
        return False
    manifest = record / MANIFEST_NAME
    if _present(manifest) and (
        not _plain(manifest, "file") or read_manifest(record) is None
    ):
        return False
    changelog = record / CHANGELOG_NAME
    if _present(changelog) and not _json_lines(changelog):
        return False
    published = record / PUBLISHED_NAME
    if _present(published) and (not _plain(published, "file") or not _json_dict(published)):
        return False
    ledger = record / LEDGER_NAME
    if _present(ledger) and (not _plain(ledger, "file") or not _json_dict(ledger)):
        return False
    pages = record / PAGES_DIR
    if _present(pages) and (not _plain(pages, "dir") or not _plain_tree(pages)):
        return False
    lock = record / LOCK_NAME
    return not _present(lock) or _plain(lock, "file")


def _production_borrower(borrower):
    model = borrower / "_model"
    record = borrower / RECORD_DIR
    return (
        borrower.is_dir()
        and not borrower.is_symlink()
        and all(_plain(item, "file") or item.name in (RECORD_DIR, "_model")
                for item in borrower.iterdir())
        and _production_record(borrower / RECORD_DIR)
        and (not _present(model) or (_plain(model, "dir") and _plain_tree(model)))
    )


def _artifact_page_name(name):
    if name.lower() in _ARTIFACT_KEPT_NAMES:
        return True
    if name.startswith("narrative--") and name.endswith(".json"):
        return True
    return any(name.endswith(suffix) for suffix in _ARTIFACT_PAGE_SUFFIXES)


def _production_artifact_folder(folder):
    if not _plain(folder, "dir"):
        return False
    for item in folder.iterdir():
        if _benign_ds_store(item):
            continue
        if not _artifact_page_name(item.name):
            return False
        if item.name.lower() == ".dashboard":
            if not _plain(item, "dir") or not _plain_tree(item):
                return False
        elif item.name.lower() == "index.json":
            if not _plain(item, "file") or not _json_dict(item):
                return False
        elif not _plain(item, "file"):
            return False
    return True


def _production_portfolio(portfolio):
    if not _plain(portfolio, "dir"):
        return False
    hosted = portfolio / HOSTED_ARTIFACT_NAME
    allowed = {ARTIFACT_DIR, HOSTED_ARTIFACT_NAME}
    for item in portfolio.iterdir():
        if item.name in allowed or _benign_ds_store(item):
            continue
        # Backups of a prior hosted-artifact.json, in the two naming shapes
        # seen in the wild. No current writer produces them; only tolerated
        # alongside a live primary file, and only if they carry the same
        # inert JSON-dict shape as that primary — never promoted or read.
        if (
            hosted.exists()
            and _HOSTED_ARTIFACT_BACKUP.fullmatch(item.name)
            and _plain(item, "file")
            and _json_dict(item)
        ):
            continue
        return False
    artifact = portfolio / ARTIFACT_DIR
    if _present(artifact) and not _production_artifact_folder(artifact):
        return False
    return not _present(hosted) or (_plain(hosted, "file") and _json_dict(hosted))


def _production_artifacts(artifacts):
    if not _plain(artifacts, "dir"):
        return False
    if any(
        item.name != PORTFOLIO_DIR and not _benign_ds_store(item)
        for item in artifacts.iterdir()
    ):
        return False
    portfolio = artifacts / PORTFOLIO_DIR
    return not _present(portfolio) or _production_portfolio(portfolio)


def _production_store(root):
    if not _plain(root, "dir"):
        return False
    items = [item for item in root.iterdir() if not _benign_ds_store(item)]
    if not items:
        return True
    if (root / PUBLISHED_NAME).exists():
        return False
    allowed = {REGISTRY_NAME, BORROWERS_DIR, ARTIFACTS_DIR, SCHEMA_NAME}
    if any(item.name not in allowed for item in items):
        return False
    registry = root / REGISTRY_NAME
    if _present(registry) and (not _plain(registry, "file") or not _json_dict(registry)):
        return False
    artifacts = root / ARTIFACTS_DIR
    if _present(artifacts) and not _production_artifacts(artifacts):
        return False
    borrowers = root / BORROWERS_DIR
    if _present(borrowers):
        if not _plain(borrowers, "dir"):
            return False
        for item in borrowers.iterdir():
            if _benign_ds_store(item):
                continue
            if not _production_borrower(item):
                return False
    return True


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
                f"{STORE_NAME} has an unsupported old store layout with a root published pointer. "
                "Only new stores with per-borrower accepted records are supported."
            )
        if any(item.is_dir() and (item / RECORD_DIR).is_dir() for item in root.iterdir() if item.name != BORROWERS_DIR):
            raise StoreError(
                f"{STORE_NAME} has an unsupported old store layout with flat borrower folders at root. "
                "Only new stores under borrowers/ are supported."
            )
        marker = root / SCHEMA_NAME
        try:
            version = json.loads(marker.read_text(encoding="utf-8")) if _plain(marker, "file") else None
        except (OSError, ValueError):
            version = None
        if version != {"schema": STORE_SCHEMA, "schema_version": STORE_SCHEMA_VERSION}:
            raise StoreError("Unsupported or unversioned store; create a new store. This folder was not changed.")
        if not _production_store(root):
            raise StoreError(
                f"{STORE_NAME} is not empty and is not a valid monitoring store."
            )
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
