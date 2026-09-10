#!/usr/bin/env python3
"""Persistence helpers for the public credit-monitoring plugin."""

from datetime import datetime, timezone
import hashlib
import json
import os
import re
import stat
import tempfile
import time

RECORD_DIR = ".record"
RUNS_DIR = "runs"
CURRENT_DIR = "current"
CONFIG_DIR = "config"
MANIFEST_NAME = "manifest.json"
CHANGELOG_NAME = "changelog.jsonl"
LEDGER_NAME = "run-ledger.json"
PUBLISHED_NAME = "published.json"
BORROWERS_DIR = "borrowers"
ARTIFACTS_DIR = "artifacts"
PAGES_DIR = "pages"
SCHEMA_NAME = "store-schema.json"
JOURNAL_NAME = "journal.json"
LOCK_NAME = "store.lock"
STORE_SCHEMA_VERSION = 1
STORE_SCHEMA = "credit-monitoring-store/v1"
PUBLISHED_SCHEMA = "credit-monitoring-published/v1"
MANIFEST_SCHEMA = "credit-monitoring-run-manifest/v1"
LEDGER_SCHEMA = "credit-monitoring-run-ledger/v1"
SKIP_SUFFIX = ".skipped.json"
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000


def resolve_path(path):
    """Return a selected root as a filesystem path."""
    return os.fspath(path)


def resolve_output_path(path):
    """Return a selected output root as a filesystem path."""
    return os.fspath(path)


def classify_store_access(path):
    """Preflight a user-selected filesystem path."""
    p = resolve_path(path)
    if not os.path.exists(p):
        return {"verdict": "missing", "path": p, "guide": None,
                "reason": f"File is missing: {p}"}
    if os.path.isfile(p) and is_cloud_only_stub(p):
        return {"verdict": "cloud_only_stub", "path": p,
                "guide": "keep-on-device",
                "reason": ("This file is kept in the cloud only, so its contents "
                           "aren't on this computer yet. Please set its folder to "
                           "'Always keep on this device', let it finish downloading, "
                           "then run me again.")}
    return {"verdict": "ok", "path": p, "guide": None, "reason": "ready"}
class StoreAccessError(ValueError):
    """A path can't be used yet — the folder isn't connected, the item isn't
    in it, or the file is a cloud-only placeholder. A ValueError subclass so a
    module's bad-input guard (except ValueError) treats it as a plain-worded
    SKIP, never a crash. Carries the machine verdict and the remedy id (which
    fix steps to show — spelled out in references/connecting-folders.md)."""

    def __init__(self, message, verdict=None, guide=None, path=None):
        super().__init__(message)
        self.verdict = verdict
        self.guide = guide
        self.path = path


def _stat_is_stub(st):
    """True when a stat result describes a OneDrive cloud-only placeholder.
    Two recipes, whichever the platform exposes: native-Windows
    RECALL_ON_DATA_ACCESS attribute, or a POSIX/WSL sparse placeholder
    (size > 0 but zero allocated blocks)."""
    attrs = getattr(st, "st_file_attributes", None)
    if attrs is not None and (attrs & _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS):
        return True
    blocks = getattr(st, "st_blocks", None)
    return blocks is not None and st.st_size > 0 and blocks == 0


def is_cloud_only_stub(path):
    """True when `path` is a OneDrive Files-On-Demand cloud-only placeholder —
    its bytes are not on the device yet, so reading it can error or silently
    truncate. Callers skip it (with the keep-on-device one-liner) rather than parse
    stub bytes. A stat error, a missing file, or a normal/empty file -> False
    (let the normal open raise its own plain error)."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return _stat_is_stub(st)


def hydrate(path, timeout=90.0, poll=1.5):
    """Last attempt to get a cloud-only file's bytes before giving up on it.

    Reading a placeholder asks OneDrive to fetch it, but only the machine's own
    OneDrive client can act on that, and a sandboxed session cannot count on
    the request reaching it. The real fix is the one-time setup: both folders
    set to "Always keep on this device" with the download finished. The setting
    alone is not the bytes — the green ticks are the signal, not the menu
    checkmark.

    Returns True when the bytes are on the device. A file that is not a stub is
    already True. Reads at most one byte -- the point is to ask for the fetch.
    """
    p = resolve_path(path)
    if not os.path.isfile(p):
        return False
    if not is_cloud_only_stub(p):
        return True
    try:
        with open(p, "rb") as fh:
            fh.read(1)
    except OSError:
        pass
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if not is_cloud_only_stub(p):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.1, float(poll)))


def ensure_readable(path):
    """Gate a file read on classify_store_access: raise StoreAccessError (a
    plain-worded skip) when the path isn't usable, else return the resolved
    on-disk path. open_model and read_spreadsheetml call this so a not-connected
    folder never reaches openpyxl / the XML parser as a traceback.

    A cloud-only file gets ONE wake attempt (hydrate) before refusal — the
    read costs nothing and was about to happen anyway, but it cannot be counted
    on. A file that stays a stub raises with the keep-on-device remedy; the
    caller sends the analyst to the guide's keep-on-device step (see
    connecting-folders.md) instead of retrying."""
    v = classify_store_access(path)
    if v["verdict"] == "cloud_only_stub" and hydrate(path):
        v = classify_store_access(path)
    if v["verdict"] != "ok":
        raise StoreAccessError(v["reason"], verdict=v["verdict"],
                               guide=v["guide"], path=v["path"])
    return resolve_path(path)


def plugin_version():
    """The shipped plugin version, read from the manifest beside this lib.

    None when the manifest is not where the plugin ships it (a bare checkout of
    the engines, a test harness) — a stamp without a version is still a stamp.
    """
    manifest = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            ".claude-plugin", "plugin.json")
    try:
        with open(manifest, encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except (OSError, ValueError):
        return None


def counterpart_path(out_path):
    """The OTHER name one module answer can carry — an output's skip record, or
    a skip record's output. A module has exactly one current answer, under
    exactly one of the two names."""
    if out_path.endswith(SKIP_SUFFIX):
        return out_path[:-len(SKIP_SUFFIX)] + ".json"
    if out_path.endswith(".json"):
        return out_path[:-len(".json")] + SKIP_SUFFIX
    return None


def retire_counterpart(out_path):
    """Delete the answer sitting under the OTHER name, so the folder reads as
    one answer instead of two that disagree.

    A module that skipped and then ran leaves both names populated, and a
    reader then reports the month twice — once reported, once skipped for a
    reason that stopped being true. A valid result may sit beside a stale skip
    record from an earlier attempt. The run folder is the record of a run, so the superseded name is deleted,
    not kept. Returns the deleted path, or None when there was no counterpart.
    Never raises: the write it follows is worth more.
    """
    other = counterpart_path(out_path)
    if not other or not os.path.exists(other):
        return None
    try:
        os.remove(other)
    except OSError:
        return None
    return other


def _atomic_write(path, write_fh):
    """Write `path` through a temp file in the same folder, then os.replace.

    `write_fh` receives an open text handle and writes the bytes. A failed
    write leaves any prior file intact and leaves no residue -- never a
    truncated file.

    The temp file's name is unique to THIS call (`tempfile.mkstemp`, in the
    destination folder, so the move stays atomic). A schedule can write a
    borrower's answer while an analyst's own session writes it too, and a
    shared `<path>.tmp` let one run truncate the other's half-written file --
    the same reason fleet.save_fleet and render_memo.publish mint their own.
    A unique name also means the `finally` below removes only this call's file.
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=os.path.basename(path) + ".",
                               suffix=".tmp")
    try:
        # mkstemp makes an owner-only file. Keep the output as readable as the
        # answer it replaces, or as readable as a plain new file.
        try:
            os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
        except OSError:
            os.chmod(tmp, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            write_fh(fh)
        os.replace(tmp, path)   # atomic on one volume; OneDrive-safe
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def write_output(out, path):
    """Resolve an output path to a writable location, ensure its parent
    directory exists, and write `out` as indented JSON ATOMICALLY (temp file in
    the same directory + os.replace). Returns the resolved path. A failed write
    leaves any prior file intact and leaves no residue — never a truncated
    output. The output-write tail every module CLI shares (each keeps its own
    result/skip print line).

    A re-run overwrites in place: the run folder holds one answer per module,
    under its plain fixed name. An answer under the
    module's OTHER name — a skip record when this is an output, an output when
    this is a skip record — is deleted (see retire_counterpart), so the folder
    holds one answer per module.

    Nothing is lost to that overwrite, because each run writes into its OWN
    folder (`run_output_folder`) and that folder is frozen at finish. The
    previous month's answers sit in the previous run's frozen folder. Writing
    into a folder that is NOT a run folder — an old-shape borrower, or an
    engine run by hand — does destroy the previous answer, and that is the
    reason the run folder exists.

    The atomic temp-file/replace mechanics live in `_atomic_write`, which
    `_write_text` shares.
    """
    out_path = resolve_output_path(path)
    _atomic_write(out_path, lambda fh: json.dump(out, fh, indent=2))
    retire_counterpart(out_path)
    return out_path


def mint_run_id(now=None):
    """`run-<YYYYMMDD>-<HHMMSS>`, in UTC -- the run folder's name.

    The orchestrator mints it once and every module output carries it, which is
    what ties one month's five answers together. The borrower is not in the id:
    the folder already sits under the borrower, and a second copy of the name
    is a second place it can disagree.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    return "run-" + stamp


def record_dir(deal_root):
    """`<deal>/.record/` -- the machine record beside the three files a person
    opens."""
    return os.path.join(deal_root, RECORD_DIR)


def current_dir(deal_root):
    """`<deal>/.record/current/` -- what the programs wrote last time."""
    return os.path.join(deal_root, RECORD_DIR, CURRENT_DIR)


def run_dir(deal_root, period, run_id):
    """`<deal>/.record/runs/<YYYY-MM>/run-<id>/` -- where THIS run writes."""
    return os.path.join(deal_root, RECORD_DIR, RUNS_DIR, period, run_id)


def deal_root_of(path):
    """The deal root above a path inside `.record/`, or None.

    None is the answer for a deal still on the old layout: an old output
    folder has no `.record/` above it. Every caller needs that answer while
    old and production-shaped folders may coexist, so it is a return value
    rather than an error.
    """
    if not path:
        return None
    here = os.path.abspath(str(path))
    while True:
        parent, name = os.path.split(here)
        if not parent or parent == here:
            return None
        if name == RECORD_DIR:
            return parent
        here = parent


def file_sha256(path):
    """`sha256:<hex>` over a file's content.

    Hydrates a cloud-only stub first (`ensure_readable`): hashing a placeholder
    hashes the placeholder, and a run folder nobody touched then reads as
    tampered with.
    """
    digest = hashlib.sha256()
    with open(ensure_readable(path), "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def folder_files(folder, skip=()):
    """Every file under `folder`, as relative forward-slash paths, sorted.

    Sorted so two runs of the same folder produce the same manifest order, and
    a manifest diff shows what moved rather than how the filesystem listed it.
    """
    found = []
    for here, dirs, names in os.walk(folder):
        dirs.sort()
        for name in names:
            rel = os.path.relpath(os.path.join(here, name),
                                  folder).replace(os.sep, "/")
            if rel not in skip:
                found.append(rel)
    return sorted(found)


def build_manifest(folder, run_id, period, by=None, finished_at=None,
                   borrower_key=None, roles=None, status="draft", edit_intake=None):
    """Hash every file in a finished run folder and write `manifest.json` last.
    Returns the manifest.

    Last on purpose: a run folder without a manifest is one a run is still
    writing, and every reader skips it. The manifest is the one file it does
    not cover, because nothing in the folder may change after it lands.

    A file that cannot be read stops the freeze and is NAMED. The reader is an
    analyst whose folder is still syncing, and the answer -- wait for that one
    file -- is only usable when the message says which file.
    """
    files = {}
    for rel in folder_files(folder, skip=(MANIFEST_NAME,)):
        try:
            files[rel] = file_sha256(os.path.join(folder, *rel.split("/")))
        except (OSError, StoreAccessError) as problem:
            raise ValueError(
                f"{rel} in the run folder {folder!r} cannot be read, so the "
                "run cannot be frozen. Let the folder finish syncing and run "
                f"this again. ({problem})") from problem
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "schema_version": STORE_SCHEMA_VERSION,
        "run_id": run_id,
        "period": period,
        "status": status,
        "published": False if status == "draft" else True,
        "plugin_version": plugin_version(),
        "finished_at": (finished_at
                        or datetime.now(timezone.utc).isoformat(timespec="seconds")),
        "files": files,
    }
    if borrower_key is not None:
        manifest["borrower_key"] = borrower_key
    if roles is not None:
        manifest["roles"] = roles
        validate_manifest_roles(folder, manifest)
    if edit_intake:
        manifest["edit_intake"] = edit_intake
    write_output(manifest, os.path.join(folder, MANIFEST_NAME))
    return manifest


def validate_manifest_roles(folder, manifest, required=("financial_workbook", "memo")):
    """Return safe manifested role paths confined to one run folder."""
    roles = (manifest or {}).get("roles")
    files = (manifest or {}).get("files")
    if not isinstance(roles, dict) or not isinstance(files, dict):
        raise ValueError("the run manifest has no readable role map.")
    missing_roles = [name for name in required if not roles.get(name)]
    if missing_roles:
        raise ValueError("the run manifest is missing roles: " + ", ".join(missing_roles))

    root = os.path.realpath(folder)
    checked = {}
    for name, value in roles.items():
        rel = str(value or "")
        parts = rel.split("/")
        if (not rel or "\\" in rel or os.path.isabs(rel)
                or re.match(r"^[A-Za-z]:", rel)
                or any(part in ("", ".", "..") for part in parts)):
            raise ValueError(f"manifest role {name!r} has an unsafe relative path.")
        if rel not in files:
            raise ValueError(f"manifest role {name!r} names an unmanifested file: {rel}")
        path = os.path.join(folder, *parts)
        if os.path.commonpath((root, os.path.realpath(path))) != root:
            raise ValueError(f"manifest role {name!r} escapes the run folder: {rel}")
        if os.path.islink(path) or not os.path.isfile(path):
            raise ValueError(f"manifest role {name!r} is not a regular file: {rel}")
        checked[name] = rel
    return checked


def read_manifest(folder):
    """A folder's manifest, or None when it has none -- which says the run is
    still being written, never that it is damaged. A malformed manifest is a
    missing one, per the repo's read convention."""
    try:
        with open(os.path.join(folder, MANIFEST_NAME), "r",
                  encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        return None
    return manifest


def verify_manifest(folder):
    """Re-hash a folder against its manifest, file by file.

    Returns `{"status", "run_id", "period", "matched", "changed", "missing",
    "unreadable", "extra"}`, each list holding relative paths. Five statuses,
    because the five have different fixes:

      ok           every file holds what the run wrote
      in-progress  no manifest -- a run is still writing this folder
      missing      the folder is not there at all -- check the folder name
      unreadable   a file could not be read, so nothing is claimed about it;
                   the folder needs to finish syncing, or be connected
      mismatch     a file changed, went missing, or was added

    A file the manifest does not name fails the verify (`extra`): a frozen
    folder holds what the run wrote and nothing else.

    In a RUN folder the manifest's own `run_id` and `period` are checked
    against the folder that holds them. They decide which half of the deal
    folder each file goes to at refresh, so a header naming another month
    sends `<borrower> PM <month>.xlsx` to the wrong half while every hash
    still matches. A manifest built for any other folder carries no such
    relationship and is not checked that way.
    """
    empty = {"matched": [], "changed": [], "missing": [], "unreadable": [],
             "extra": []}
    if not os.path.isdir(folder):
        return dict(empty, status="missing", run_id=None, period=None)
    manifest = read_manifest(folder)
    if manifest is None:
        return dict(empty, status="in-progress", run_id=None, period=None)
    want = manifest.get("files") or {}
    here = os.path.normpath(os.path.abspath(folder))
    period_dir, run_name = os.path.split(here)
    runs_dir, period_name = os.path.split(period_dir)
    is_run_folder = (os.path.basename(runs_dir) == RUNS_DIR
                     and deal_root_of(here) is not None)
    header_wrong = [
        label for label, said, holds in (
            ("run_id", manifest.get("run_id"), run_name),
            ("period", manifest.get("period"), period_name),
        ) if is_run_folder and said is not None and str(said) != holds
    ]
    matched, changed, missing, unreadable = [], [], [], []
    for rel, digest in sorted(want.items()):
        full = os.path.join(folder, *rel.split("/"))
        if not os.path.exists(full):
            missing.append(rel)
            continue
        try:
            got = file_sha256(full)
        except (OSError, StoreAccessError):
            unreadable.append(rel)
            continue
        (matched if got == digest else changed).append(rel)
    extra = [rel for rel in folder_files(folder, skip=(MANIFEST_NAME,))
             if rel not in want]
    if changed or missing or extra or header_wrong:
        status = "mismatch"
        changed = changed + [f"{MANIFEST_NAME} ({label})"
                             for label in header_wrong]
    elif unreadable:
        status = "unreadable"
    else:
        status = "ok"
    return {"status": status, "run_id": manifest.get("run_id"),
            "period": manifest.get("period"), "matched": matched,
            "changed": changed, "missing": missing, "unreadable": unreadable,
            "extra": extra}
