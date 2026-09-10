"""Local store writes: path safety, a working-state fingerprint, and durable
single-file replacement.

Finish/accept/discard apply their planned writes directly, file by file, in a
fixed order -- there is no multi-file journal and no cross-file atomicity.
Each write is itself durable (temp file + os.replace), and every caller's
guard checks (stale-run, unrecorded-edit) run before any write, under the
same local lock. A run interrupted mid-write is recovered by calling the
same operation again: the guard checks and the writes they compute are
re-derived from current disk state, so a retry against the same saved run
completes whatever is left. The caller holds the borrower lock; this is not
a cross-machine sync protocol.
"""

import hashlib
import os
from pathlib import Path
import shutil
import tempfile

try:
    from plugin.lib.persistence import file_sha256
except ModuleNotFoundError:
    from persistence import file_sha256


class StoreRecoveryError(ValueError):
    """A pre-existing recovery record needs manual review before any write."""


METADATA = {".record/manifest.json", ".record/published.json", ".record/changelog.jsonl",
            ".record/edit-intake.json"}


def path_in(root, rel):
    if (not isinstance(rel, str) or not rel or "\\" in rel or ":" in rel
            or any(p in ("", ".", "..") for p in rel.split("/"))):
        raise ValueError("unsafe store path")
    root = Path(root).absolute()
    target = root
    for part in rel.split("/"):
        target = target / part
        if target.is_symlink():
            raise ValueError(f"symbolic link in store path: {rel}")
    return target


def digest(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"not a regular store file: {path}")
    return file_sha256(path) if path.exists() else None


def text_digest(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot(root):
    """Capture all visible files, working settings, and decision metadata.

    Used to pin a run's delivery base at open and to detect a stale run at
    finish/accept/discard time -- a fingerprint of working state, not part of
    any write-time validation."""
    root = Path(root)
    names = set(METADATA)
    if root.exists():
        for item in root.iterdir():
            if item.is_symlink():
                raise ValueError(f"symbolic link in borrower folder: {item.name}")
            if item.is_file():
                names.add(item.name)
    current = path_in(root, ".record/current")
    if current.exists():
        if not current.is_dir():
            raise ValueError("the current folder is not a directory")
        for item in current.rglob("*"):
            if item.is_symlink():
                raise ValueError(f"symbolic link in current folder: {item.name}")
            if item.is_file():
                names.add(item.relative_to(root).as_posix())
    return {rel: digest(path_in(root, rel)) for rel in sorted(names)}


def atomic_file(path, *, text=None, source=None):
    """Durable file replacement; temp names are unique to this write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".store-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            if source is not None:
                with open(source, "rb") as incoming:
                    shutil.copyfileobj(incoming, out)
            else:
                out.write(text.encode("utf-8"))
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
