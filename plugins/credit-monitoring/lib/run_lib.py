#!/usr/bin/env python3
"""Shared plumbing for the credit-monitoring plugin.

This is the small public adapter layer around the persisted store:

- portable `source:` / `store:` path handling;
- the run-folder helpers used by finish / resume / publish;
- immutable run manifest and current-result selection helpers.

The calculation engine remains in `monitor_lib.py`; this module keeps the run lifecycle separate.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    from plugin.lib.persistence import *  # noqa: F401,F403 - shared primitives
    from plugin.lib import store_transaction as transaction
    from plugin.lib import workbook_integrity as integrity
except ModuleNotFoundError:
    from persistence import *  # noqa: F401,F403 - extracted plugin script
    import store_transaction as transaction
    import workbook_integrity as integrity  # noqa: E402 - extracted plugin script

StoreRecoveryError = transaction.StoreRecoveryError


SOURCE_ROOT_ENV = "CREDIT_MONITOR_SOURCE_ROOT"
STORE_ROOT_ENV = "CREDIT_MONITOR_STORE_ROOT"
SOURCE_PREFIX = "source"
STORE_PREFIX = "store"
PUBLISHED_NAME = "published.json"
LEDGER_NAME = "run-ledger.json"
MANIFEST_NAME = "manifest.json"
RECORD_DIR = ".record"
CURRENT_DIR = "current"
RUNS_DIR = "runs"
LOCK_NAME = "store.lock"
JOURNAL_NAME = "journal.json"
EDIT_INTAKE_NAME = "edit-intake.json"
EDIT_INTAKE_SCHEMA = "credit-monitoring-edit-intake/v1"


class ConfigError(ValueError):
    """A configured path or store root is missing or malformed."""


class StoreLockError(RuntimeError):
    """Another local process already holds the store lock for this borrower."""


def _lock_path(deal_root):
    return os.path.join(record_dir(deal_root), LOCK_NAME)


@contextmanager
def store_lock(deal_root):
    """Serialize finish/accept/discard/edit-intake for one borrower.

    `fcntl.flock` only serializes processes on the SAME machine; it is not a
    distributed lock across machines sharing a cloud-synced folder. A
    pre-existing `.record/journal.json` is left over from an older plugin
    version that recorded a multi-file recovery journal; this version does
    not read or replay it, so it is reported and left untouched rather than
    silently converted, replayed or discarded.
    """
    _reject_deal_alias(deal_root)
    deal = Path(deal_root).absolute()
    marker = transaction.path_in(deal.parent.parent, SCHEMA_NAME)
    try:
        version = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("unsupported or unversioned store; create a new store without changing this one") from exc
    if (deal.parent.name != BORROWERS_DIR
            or version != {"schema": STORE_SCHEMA, "schema_version": STORE_SCHEMA_VERSION}
            or (deal.parent.parent / PUBLISHED_NAME).exists()):
        raise ValueError("unsupported store layout or version; this store was not changed")
    path = transaction.path_in(deal_root, ".record/" + LOCK_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise StoreLockError(
                f"another local session is already changing {deal_root!r}; "
                "wait for it to finish and try again.") from exc
        _refuse_legacy_journal(deal_root)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def _refuse_legacy_journal(deal_root):
    path = transaction.path_in(deal_root, ".record/" + JOURNAL_NAME)
    if path.exists():
        raise StoreRecoveryError(
            f"{path} is a recovery journal from an older plugin version; this "
            "version does not read or replay it. Preserve this store for "
            "investigation and use a fresh output folder for candidate QA. "
            "Do not reconstruct or delete the record to bypass this check.")


def _write_digest(deal_root, value):
    if value is None:
        return None
    return (transaction.text_digest(value["text"]) if "text" in value else
            transaction.digest(transaction.path_in(deal_root, value["source"])))


def _guard_write_retry(deal_root, before, writes):
    """Use existing run hashes, not a recovery file, to recognize a partial copy."""
    if not isinstance(before, dict):
        raise ValueError("this run has no pinned delivery base; start a new run")
    now = transaction.snapshot(deal_root)
    for rel in set(before) | set(now) | set(writes):
        allowed = [before.get(rel)]
        if rel in writes:
            allowed.append(_write_digest(deal_root, writes[rel]))
        if now.get(rel) not in allowed:
            raise ValueError(f"stale or edited working file: {rel}; retry would overwrite changed work")


def _apply_writes(deal_root, writes):
    """Apply planned working-copy writes, in the given (dict) order, each as
    one durable single-file replace or delete. Not a multi-file transaction:
    an interruption here leaves whatever prefix of `writes` already landed,
    and the caller's own operation is retried from scratch -- its guard
    checks and its `writes` are recomputed from current disk state, so a
    retry against the same saved run finishes what is left rather than
    redoing what already landed."""
    before = transaction.snapshot(deal_root)
    for rel, value in writes.items():
        target = transaction.path_in(deal_root, rel)
        actual = transaction.digest(target)
        if actual == _write_digest(deal_root, value):
            continue
        if actual != before.get(rel):
            raise ValueError(f"working file changed during delivery: {rel}")
        if value is None:
            if target.exists():
                target.unlink()
                transaction.sync_directory(target.parent)
        elif "text" in value:
            transaction.atomic_file(target, text=value["text"])
        else:
            source = transaction.path_in(deal_root, value["source"])
            if not source.exists():
                raise ValueError(f"preserved source is missing: {value['source']}")
            transaction.atomic_file(target, source=source)


def _json_write(value):
    return {"text": json.dumps(value, indent=2, ensure_ascii=False)}


def _decision_write(deal_root, event):
    path = transaction.path_in(deal_root, ".record/" + CHANGELOG_NAME)
    _read_jsonl(path)  # Refuse malformed history before changing any state.
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return {"text": text.rstrip("\n") + ("\n" if text else "")
            + json.dumps(event, ensure_ascii=False) + "\n"}


def delivery_base(deal_root):
    """Pinned at run open, across all periods, so a late writer cannot win."""
    with store_lock(deal_root):
        return transaction.snapshot(deal_root)


def _now():
    return datetime.now(timezone.utc)


def _norm_rel(path):
    raw = str(path or "").strip()
    rel = raw.replace("\\", "/")
    if (not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel)
            or rel in (".", "..") or "/../" in f"/{rel}/"
            or "/./" in f"/{rel}/"):
        raise ConfigError(f"unsafe relative path: {path!r}")
    return rel.rstrip("/")


def parse_store_path(path):
    """Return (`source`|`store`, relative path) for a portable configured path."""
    if path is None:
        return None
    text = str(path).strip()
    if ":" not in text:
        return None
    root, rel = text.split(":", 1)
    if root not in (SOURCE_PREFIX, STORE_PREFIX):
        if re.match(r"^[A-Za-z][A-Za-z0-9_]*:(?![\\/])", text):
            raise ConfigError(
                f"unsupported logical root in {path!r}; use source: or store:.")
        return None
    return root, _norm_rel(rel)


def _root_env(store_id):
    if store_id == SOURCE_PREFIX:
        return SOURCE_ROOT_ENV
    if store_id == STORE_PREFIX:
        return STORE_ROOT_ENV
    return None


def store_root(store_id):
    env = _root_env(store_id)
    if not env:
        return None
    root = os.environ.get(env)
    return os.path.abspath(root) if root else None


def _resolve_prefixed(path):
    parsed = parse_store_path(path)
    if parsed is None:
        return None
    store_id, rel = parsed
    root = store_root(store_id)
    if not root:
        raise ConfigError(
            f"the {store_id!r} root is not configured; set { _root_env(store_id) }"
        )
    return os.path.join(root, *rel.split("/"))


def resolve_path(path):
    """Resolve a configured portable path or pass through a raw local path."""
    resolved = _resolve_prefixed(path)
    return os.path.abspath(resolved) if resolved else os.fspath(path)


def resolve_output_path(path):
    return resolve_path(path)


def resolve_store_output_path(path, store_id=STORE_PREFIX):
    """Resolve a configured store path and assert it belongs to the store root."""
    parsed = parse_store_path(path)
    if parsed is None:
        return resolve_path(path)
    sid, _ = parsed
    if sid != store_id:
        raise ConfigError(f"expected {store_id!r} path, got {sid!r}")
    return resolve_path(path)


def unresolve_path(path):
    """Convert a path under the configured source/store roots back to portable form."""
    if path is None:
        return None
    here = Path(os.fspath(path)).expanduser().resolve()
    for store_id in (SOURCE_PREFIX, STORE_PREFIX):
        root = store_root(store_id)
        if not root:
            continue
        root_path = Path(root).resolve()
        try:
            rel = here.relative_to(root_path)
        except ValueError:
            continue
        return f"{store_id}:{rel.as_posix()}"
    return os.fspath(path)


def store_of(path, catalog=None):
    parsed = parse_store_path(path)
    if parsed:
        return parsed[0], parsed[1], None
    return None, None, None


def store_link(url_base, path_in_store):
    """No runtime URL catalog is available in the public adapter."""
    return None


def store_catalog_entry(store_id, catalog=None):
    """No runtime catalog layer is available in the public adapter."""
    return None


def load_config(path):
    with open(resolve_path(path), encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"configuration is not a JSON object: {path}")
    return data


def mint_run_id(now=None):
    stamp = (now or _now()).strftime("%Y%m%d-%H%M%S")
    return "run-" + stamp


def run_stamp(run_id=None, module=None):
    now = _now().isoformat(timespec="seconds")
    return {
        "run_id": run_id or mint_run_id(),
        "run_id_from": "provided" if run_id else "minted",
        "produced_at": now,
        "produced_by": module,
    }


def period_stamp(period):
    if period is None:
        return None
    if isinstance(period, (list, tuple)):
        for item in reversed(period):
            stamped = period_stamp(item)
            if stamped is not None:
                return stamped
        return None
    text = str(period).strip()
    m = re.match(r"^(\d{4})-(0[1-9]|1[0-2])$", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.match(r"^(\d{4})-(0[1-9]|1[0-2])", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def conclusion(module, period, results, exceptions=None, source_files=None,
               run_id=None, **meta):
    out = {
        "module": module,
        "period": period_stamp(period),
        "results": results or [],
        "exceptions": exceptions or [],
        "source_files": source_files or [],
    }
    out.update(run_stamp(run_id, module))
    out.update(meta)
    return out


class SourceIndex:
    """Compact path index for source refs."""

    def __init__(self, catalog=None):
        self.catalog = catalog or {}
        self.source_files = []
        self._positions = {}
        self.stores = {}
        self.reach = {}

    def ref(self, path, **fields):
        resolved = unresolve_path(str(path).strip())
        if resolved not in self._positions:
            shown = resolved
            if not parse_store_path(shown) and (
                    Path(shown).is_absolute()
                    or re.match(r"^(?:[A-Za-z]:[\\/]|[\\/]{2})", shown)):
                shown = shown.replace("\\", "/").rsplit("/", 1)[-1]
                if parse_store_path(shown):
                    shown = "unlocated-" + shown.replace(":", "-")
            if shown in self.source_files:
                stem, suffix = os.path.splitext(shown)
                n = 2
                while f"{stem} [{n}]{suffix}" in self.source_files:
                    n += 1
                shown = f"{stem} [{n}]{suffix}"
            self._positions[resolved] = len(self.source_files)
            self.source_files.append(shown)
        idx = self._positions[resolved]
        out = {"file": idx, **fields}
        store_id, path_in_store, _ = store_of(resolved, self.catalog)
        if store_id:
            out["store"] = store_id
            out["path_in_store"] = path_in_store
            self.stores[store_id] = True
            self.reach[store_id] = self.reach.get(store_id, 0) + 1
        return out


def deal_file_names(borrower, period):
    return (
        f"{borrower} Monthly IS-BS-CF.xlsx",
        f"{borrower} Monitoring Memo.html",
    )


def run_output_folder(monitor_output_folder, period, run_id):
    parsed = parse_store_path(monitor_output_folder)
    if parsed:
        store_id, rel = parsed
        tail = f"/{RECORD_DIR}/{CURRENT_DIR}"
        rel = rel.rstrip("/")
        if not rel.endswith(tail):
            return monitor_output_folder
        deal_rel = rel[:-len(tail)]
        return f"{store_id}:{deal_rel}/{RECORD_DIR}/{RUNS_DIR}/{period}/{run_id}"
    here = os.path.normpath(os.fspath(monitor_output_folder))
    parent, name = os.path.split(here)
    if name != CURRENT_DIR or os.path.basename(parent) != RECORD_DIR:
        return monitor_output_folder
    return run_dir(os.path.dirname(parent), period, run_id)


def _reject_deal_alias(deal_root):
    if Path(deal_root).expanduser().is_symlink():
        raise ValueError(f"the borrower folder {deal_root!r} must not be a symbolic link.")


def _selection_borrower(deal_root):
    return Path(deal_root).expanduser().resolve().name


def _changelog_path(deal_root):
    return os.path.join(record_dir(deal_root), CHANGELOG_NAME)


def _published_entry(data, period):
    """The accepted run id for `period`, or None. `data` is one borrower's
    own `published.json` contents -- there is no other borrower's data to
    key past, since the file lives under that borrower's own `.record/`."""
    entry = ((data or {}).get("months") or {}).get(period)
    return entry.get("run") if isinstance(entry, dict) else None


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("history event is not an object")
                rows.append(event)
            except ValueError as exc:
                raise ValueError(f"malformed selection changelog at {path}") from exc
    return rows


def _latest_selection_event(deal_root, borrower, period):
    latest = None
    for event in _read_jsonl(_changelog_path(deal_root)):
        if (event.get("event") == "current-result-selected"
                and str(event.get("borrower")) == str(borrower)
                and str(event.get("period")) == str(period)):
            latest = event
    return latest


def _discard_event(deal_root, borrower, period, run_id):
    """Find this run's discard decision so a retry cannot append it twice."""
    for event in _read_jsonl(_changelog_path(deal_root)):
        if (event.get("event") == "discarded"
                and str(event.get("borrower")) == str(borrower)
                and str(event.get("period")) == str(period)
                and str(event.get("run_id")) == str(run_id)):
            return event
    return None


def _immutable_run_folder(deal, period, run_id):
    period, run_id = str(period or ""), str(run_id or "")
    if (not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period)
            or not re.fullmatch(r"run-\d{8}-\d{6}", run_id)):
        raise ValueError("period and run id do not name one verified immutable run.")
    return run_dir(deal, period, run_id)


def verified_run(monitor_output_folder, period, run_id, catalog=None):
    """Resolve one immutable run directly, independent of current selection."""
    deal = deal_root_of(resolve_output_path(monitor_output_folder))
    if deal is None:
        raise ValueError(
            f"{monitor_output_folder!r} is not a folder in the credit monitoring store.")
    folder = _immutable_run_folder(deal, period, run_id)
    checked = verify_manifest(folder)
    if checked["status"] != "ok":
        raise ValueError(
            f"{folder!r} does not verify against its own manifest ({checked['status']}).")
    manifest = read_manifest(folder)
    borrower = _selection_borrower(deal)
    if manifest.get("borrower_key") not in (None, borrower):
        raise ValueError(
            f"the manifest borrower {manifest.get('borrower_key')!r} does not match {borrower!r}.")
    roles = validate_manifest_roles(folder, manifest)
    ledger = read_ledger(folder)
    if ledger is not None and (str(ledger.get("run_id")) != str(run_id)
                               or str(ledger.get("period")) != str(period)):
        raise ValueError("the run ledger identity does not match its folder.")
    return {
        "period": period, "run_id": run_id, "folder": folder,
        "manifest": manifest, "roles": roles,
        "workbook_path": os.path.join(folder, *roles["financial_workbook"].split("/")),
        "memo_path": os.path.join(folder, *roles["memo"].split("/")),
    }


def _selected_run_guard(monitor_output_folder, period, run_id, catalog=None):
    found = verified_run(monitor_output_folder, period, run_id, catalog)
    deal = deal_root_of(resolve_output_path(monitor_output_folder))
    borrower = _selection_borrower(deal)
    latest = _latest_selection_event(deal, borrower, period)
    if latest is None:
        raise ValueError(
            f"published.json names {borrower}'s {period} as {run_id}, but there is no "
            "current-result-selected event for that month.")
    if str(latest.get("run_id")) != str(run_id):
        raise ValueError(
            f"published.json names {borrower}'s {period} as {run_id}, but the latest "
            f"current-result-selected event names {latest.get('run_id')}.")
    return found


def published_path(deal_root):
    """`<borrower>/.record/published.json` -- the ONE accepted-run pointer for
    this borrower. Never a store-root path: a root-level pointer would name
    an accepted run for every borrower at once, which is exactly the
    competing pointer the store contract forbids."""
    return os.path.join(record_dir(deal_root), PUBLISHED_NAME)


def read_published(deal_root):
    path = published_path(deal_root)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if (not isinstance(data, dict) or data.get("schema") != PUBLISHED_SCHEMA
            or type(data.get("schema_version")) is not int
            or data["schema_version"] != STORE_SCHEMA_VERSION
            or data.get("borrower") != _selection_borrower(deal_root)
            or not isinstance(data.get("months"), dict)):
        raise ValueError(f"{path} has an unsupported or malformed accepted-record schema.")
    for period, entry in data["months"].items():
        if not isinstance(entry, dict):
            raise ValueError("accepted period does not name one immutable run")
        _immutable_run_folder(deal_root, period, entry.get("run"))
    return data


def published_run(monitor_output_folder, period, run_id, catalog=None):
    return _selected_run_guard(monitor_output_folder, period, run_id, catalog)


def latest_published(monitor_output_folder, before_period=None, catalog=None):
    deal = deal_root_of(resolve_output_path(monitor_output_folder))
    if deal is None:
        raise ValueError(
            f"{monitor_output_folder!r} is not a folder in the credit monitoring store.")
    data = read_published(deal)
    months = data.get("months") or {}
    if not isinstance(months, dict):
        raise ValueError(f"{published_path(deal)} does not hold one run id per month.")
    periods = sorted(p for p in months if before_period is None or str(p) < str(before_period))
    if not periods:
        return None
    run_id = _published_entry(data, periods[-1])
    return published_run(monitor_output_folder, periods[-1], str(run_id), catalog)


def read_ledger(run_folder):
    try:
        with open(ledger_path(run_folder), encoding="utf-8") as fh:
            ledger = json.load(fh)
    except (OSError, ValueError):
        return None
    return ledger if isinstance(ledger, dict) else None


LEDGER_STEPS = ("config", "model", "modules", "coverage", "memo", "write_back")
LEDGER_STATES = ("pending", "done", "skipped")
MODEL_SIDECARS = ("source-map.json", "restatements.json")
EXECUTION_CONFIG_NAME = "execution-config.json"


def new_ledger(run_id, period, by=None, base=None, modules=(),
               analyst_focus=None, packages_seen=None, started_at=None, deal_root=None):
    return {
        "schema": LEDGER_SCHEMA, "schema_version": STORE_SCHEMA_VERSION,
        **({"delivery_base": delivery_base(deal_root)} if deal_root is not None else {}),
        "run_id": run_id,
        "period": period,
        "status": "in-flight",
        "started_at": (started_at or _now().isoformat(timespec="seconds")),
        "base": base or {"from": "seed"},
        "packages_seen": list(packages_seen or []),
        "analyst_focus": list(analyst_focus or []),
        "steps": dict({name: "pending" for name in LEDGER_STEPS},
                      modules={str(m): "pending" for m in modules}),
    }


def ledger_path(run_folder):
    return os.path.join(run_folder, LEDGER_NAME)


def mark_step(run_folder, step, state="done", module=None):
    run_folder = resolve_output_path(run_folder)
    deal = deal_root_of(run_folder)
    if deal is None:
        raise ValueError("run is not inside a borrower record")
    with store_lock(deal):
        if Path(run_folder, MANIFEST_NAME).exists():
            raise ValueError("the run is frozen; its ledger cannot be changed")
        return _mark_step(run_folder, step, state, module)


def _mark_step(run_folder, step, state, module):
    if state not in LEDGER_STATES:
        raise ValueError(f"{state!r} is not a step state.")
    ledger = read_ledger(run_folder)
    if ledger is None:
        raise ValueError(f"there is no run ledger at {ledger_path(run_folder)}.")
    steps = ledger.setdefault("steps", {})
    if module is not None:
        steps.setdefault("modules", {})[str(module)] = state
    elif step == "modules":
        raise ValueError("the modules step is a map, so pass the module name.")
    else:
        if step not in LEDGER_STEPS:
            raise ValueError(f"{step!r} is not a step of a run.")
        steps[step] = state
    write_output(ledger, ledger_path(run_folder))
    return ledger


def pending_steps(ledger):
    steps = (ledger or {}).get("steps")
    if not isinstance(steps, dict):
        return list(LEDGER_STEPS)
    waiting = []
    for name in LEDGER_STEPS:
        if name == "modules":
            per_module = steps.get("modules")
            if not isinstance(per_module, dict):
                waiting.append("modules")
                continue
            waiting += [f"modules: {mod}" for mod, state in sorted(per_module.items())
                        if state not in ("done", "skipped")]
        elif steps.get(name) not in ("done", "skipped"):
            waiting.append(name)
    return waiting


def run_snapshot_state(run_folder, ledger=None):
    ledger = ledger or read_ledger(run_folder) or {}
    inputs = ledger.get("inputs") or {}
    model_rel = inputs.get("model")
    config_rel = inputs.get("config")
    if not model_rel or not config_rel:
        return {"model_path": None, "config_folder": None,
                "deal_context_path": None, "execution_config_path": None}
    model_path = os.path.join(run_folder, *model_rel.split("/"))
    config_folder = os.path.join(run_folder, *config_rel.split("/"))
    if not os.path.isfile(model_path) or not os.path.isdir(config_folder):
        raise ValueError("the completed model snapshot is missing from the run folder.")
    context = os.path.join(config_folder, "deal-context.json")
    execution = os.path.join(
        config_folder, inputs.get("execution_config") or EXECUTION_CONFIG_NAME)
    if inputs.get("execution_config") and not os.path.isfile(execution):
        raise ValueError("the completed execution config snapshot is missing from the run folder.")
    return {
        "model_path": model_path,
        "config_folder": config_folder,
        "deal_context_path": context if os.path.isfile(context) else None,
        "execution_config_path": execution if os.path.isfile(execution) else None,
    }


def _snapshot_store_path(path):
    logical = unresolve_path(path)
    parsed = parse_store_path(logical)
    if not parsed or parsed[0] != STORE_PREFIX:
        raise ConfigError("run snapshots must resolve under the bound store root.")
    return logical


def _execution_config(config_path, config_target, model_target):
    execution = load_config(config_path)
    paths = execution.get("paths")
    if not isinstance(paths, dict):
        raise ConfigError("borrower config paths must be a JSON object.")
    config_source = Path(config_path).resolve().parent
    for key, value in list(paths.items()):
        parsed = parse_store_path(value)
        if not parsed or parsed[0] != STORE_PREFIX:
            continue
        try:
            rel = Path(resolve_path(value)).resolve().relative_to(config_source)
        except ValueError:
            continue
        copied = os.path.join(config_target, *rel.parts)
        if os.path.isfile(resolve_path(value)):
            paths[key] = _snapshot_store_path(copied)
    paths["model_output_path"] = _snapshot_store_path(model_target)
    return execution


def snapshot_run_inputs(run_folder, config_path, model_path):
    """Copy complete controls and stable model inputs once, then pin them in the ledger."""
    run_folder = resolve_output_path(run_folder)
    deal = deal_root_of(run_folder)
    if deal is None:
        raise ValueError("run is not inside a borrower record")
    with store_lock(deal):
        if Path(run_folder, MANIFEST_NAME).exists():
            raise ValueError("the run is frozen; its inputs cannot be changed")
        return _snapshot_run_inputs(run_folder, config_path, model_path)


def _consume_edit_sidecar_source(run_folder, ledger):
    """Where a pinned run's stable-model sidecars (source-map, restatements)
    come from: the verified base run ordinary `open` already resolved into
    `ledger["base"]` when one exists; otherwise the canonical `_model/`
    workspace when it holds both sidecars. The borrower root itself never
    holds sidecars -- only the delivered workbook the analyst edited."""
    base = ledger.get("base") or {}
    if base.get("from") != "run" or not base.get("run_id") or not base.get("period"):
        deal = deal_root_of(run_folder)
        canonical = Path(deal) / "_model"
        if canonical.is_dir() and all((canonical / name).is_file() for name in MODEL_SIDECARS):
            return str(canonical)
        raise ValueError(
            "a run pinned to consume an accepted edit needs verified model "
            "sidecars from its prior run or canonical model workspace; none "
            "were found.")
    deal = deal_root_of(run_folder)
    base_folder = _immutable_run_folder(deal, str(base["period"]), str(base["run_id"]))
    checked = verify_manifest(base_folder)
    if checked["status"] != "ok":
        raise ValueError(
            f"this run's base {base_folder!r} no longer verifies against its "
            f"own manifest ({checked['status']}); its sidecars cannot be trusted.")
    return base_folder


def _snapshot_run_inputs(run_folder, config_path, model_path):
    ledger = read_ledger(run_folder)
    if ledger is None:
        raise ValueError(f"there is no run ledger at {ledger_path(run_folder)}.")
    if (ledger.get("steps") or {}).get("model") == "done":
        return run_snapshot_state(run_folder, ledger)

    config_path = resolve_path(config_path)
    model_path = resolve_path(model_path)
    config_source = os.path.dirname(config_path)

    pin = ledger.get("consume_edit")
    if pin is not None:
        # This run was opened to consume one specific accepted edit; prove the
        # model actually being staged is that exact reviewed content, not
        # something else the caller passed by mistake, BEFORE trusting
        # anything else about this snapshot.
        if not os.path.isfile(model_path) or file_sha256(model_path) != pin.get("digest"):
            raise ValueError(
                f"this run is pinned to consume the accepted edit at {pin.get('path')!r} "
                f"(digest {pin.get('digest')}); the given model does not match it.")
        sidecar_dir = _consume_edit_sidecar_source(run_folder, ledger)
    else:
        sidecar_dir = os.path.dirname(model_path)

    sources = [model_path] + [os.path.join(sidecar_dir, name) for name in MODEL_SIDECARS]
    if not os.path.isfile(config_path) or not os.path.isdir(config_source):
        raise ValueError("the borrower config folder is not readable.")
    missing = [os.path.basename(path) for path in sources if not os.path.isfile(path)]
    if missing:
        raise ValueError("the stable model snapshot is missing: " + ", ".join(missing))

    config_target = os.path.join(run_folder, CONFIG_DIR)
    targets = [os.path.join(run_folder, os.path.basename(path)) for path in sources]
    if os.path.exists(config_target) or any(os.path.exists(path) for path in targets):
        raise ValueError("the run already contains an incomplete input snapshot; refusing to overwrite it.")
    execution = _execution_config(config_path, config_target, targets[0])
    shutil.copytree(config_source, config_target)
    for source, target in zip(sources, targets):
        _copy_into_place(source, target)
    write_output(execution, os.path.join(config_target, EXECUTION_CONFIG_NAME))

    ledger["inputs"] = {
        "config": CONFIG_DIR,
        "model": os.path.basename(model_path),
        "sidecars": [os.path.basename(path) for path in sources[1:]],
        "execution_config": EXECUTION_CONFIG_NAME,
    }
    ledger.setdefault("steps", {})["config"] = "done"
    ledger["steps"]["model"] = "done"
    write_output(ledger, ledger_path(run_folder))
    return run_snapshot_state(run_folder, ledger)


def resumable_run(monitor_output_folder, period):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(period or "")):
        raise ValueError(f"invalid monitoring period: {period!r}.")
    deal = deal_root_of(resolve_output_path(monitor_output_folder))
    if deal is None:
        return None
    month_dir = os.path.join(deal, RECORD_DIR, RUNS_DIR, period)
    if not os.path.isdir(month_dir):
        return None
    for name in sorted(os.listdir(month_dir), reverse=True):
        here = os.path.join(month_dir, name)
        if not os.path.isdir(here) or read_manifest(here) is not None:
            continue
        ledger = read_ledger(here)
        if ledger:
            if (str(ledger.get("period")) != str(period)
                    or str(ledger.get("run_id")) != name):
                raise ValueError("the run ledger identity does not match its folder.")
            return {"run_id": name, "folder": here, "ledger": ledger}
    return None


def adopt_into_run(run_folder, source_path):
    if not source_path:
        return None
    here = resolve_path(source_path)
    if not os.path.isfile(here):
        return None
    name = os.path.basename(os.path.normpath(here))
    target = os.path.join(run_folder, name)
    if os.path.exists(target):
        return name
    _copy_into_place(here, target)
    return name


def _zone_of(rel, deal_names):
    return "deal" if rel in deal_names else "current"


def repoint_refs(run_folder, deal_root, period=None, catalog=None, roles=None):
    deal_rel = unresolve_path(deal_root)
    run_rel = unresolve_path(run_folder)
    if not deal_rel or not run_rel:
        return {}
    borrower = os.path.basename(os.path.normpath(deal_root))
    period = period or os.path.basename(os.path.dirname(os.path.normpath(run_folder)))
    deal_names = set((roles or {}).get(name) for name in ("financial_workbook", "memo"))
    deal_names.discard(None)
    if not deal_names:
        deal_names = set(deal_file_names(borrower, period))
    changed = {}
    for rel in folder_files(run_folder, skip=(MANIFEST_NAME,)):
        if rel.startswith(CONFIG_DIR + "/") or rel == CONFIG_DIR:
            continue
        full = os.path.join(run_folder, *rel.split("/"))
        try:
            with open(full, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        old = text
        if _zone_of(rel, deal_names) == "deal":
            text = text.replace(deal_rel, run_rel)
        else:
            text = text.replace(f"{deal_rel}/{RECORD_DIR}/{CURRENT_DIR}", run_rel)
        if text != old:
            _write_text(full, text)
            changed[rel] = 1
    return changed


def _delivery_writes(run_folder, deal_root, status="draft"):
    """Plan working-copy changes from a verified run; do not touch disk yet."""
    period, run_id = Path(run_folder).parent.name, Path(run_folder).name
    found = verified_run(current_dir(deal_root), period, run_id)
    manifest, roles = found["manifest"], found["roles"]
    if Path(found["folder"]).resolve() != Path(run_folder).resolve():
        raise ValueError("run is not inside this borrower")
    names = {roles["financial_workbook"], roles["memo"]}
    if any("/" in rel for rel in names):
        raise ValueError("visible workbook and memo must be plain borrower file names")
    writes = {}
    for rel in manifest["files"]:
        source = f".record/runs/{period}/{run_id}/{rel}"
        transaction.path_in(deal_root, source)
        value = {"source": source}
        if rel in names:
            writes[rel] = value
        if rel not in names or rel == roles["financial_workbook"]:
            writes[".record/current/" + rel] = value
    outgoing = read_manifest(record_dir(deal_root)) or {}
    old_names = set((outgoing.get("roles") or {}).get(name)
                    for name in ("financial_workbook", "memo")) - {None}
    for rel in old_names - names:
        writes[rel] = None
    # Retire only recorded files. Unrecognized user files are never deleted.
    for rel in outgoing.get("files", {}):
        target = ".record/current/" + rel
        if target not in writes:
            writes[target] = None
    if os.path.exists(_edit_intake_path(deal_root)):
        writes[".record/" + EDIT_INTAKE_NAME] = None
    # The working manifest moves last: it identifies a complete delivery.
    writes[".record/manifest.json"] = _json_write(
        dict(manifest, status=status, published=status == "accepted"))
    return writes


def _copy_into_place(source, target):
    parent = os.path.dirname(target) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, f".{os.path.basename(target)}.{os.getpid()}.tmp")
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _atomic_write(path, writer):
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            writer(fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _write_text(path, text):
    _atomic_write(path, lambda fh: fh.write(text))


def _accept_writes(deal_root, run_id, period):
    borrower = _selection_borrower(deal_root)
    data = read_published(deal_root)
    event = _latest_selection_event(deal_root, borrower, period)
    manifest = read_manifest(record_dir(deal_root))
    writes = {}
    at = _now().isoformat(timespec="seconds")
    if _published_entry(data, period) != run_id:
        data.update(schema=PUBLISHED_SCHEMA, schema_version=STORE_SCHEMA_VERSION,
                    borrower=borrower)
        data.setdefault("months", {})[period] = {"run": run_id, "at": at}
        writes[".record/published.json"] = _json_write(data)
    if not (event and str(event.get("run_id")) == str(run_id)):
        writes[".record/changelog.jsonl"] = _decision_write(deal_root, {
            "at": at, "event": "current-result-selected", "borrower": borrower,
            "period": period, "run_id": run_id})
    writes[".record/manifest.json"] = _json_write(
        dict(manifest, status="accepted", published=True))
    return writes


def select_current_result(run_folder, deal_root, period=None, keep_unpublished=False,
                          catalog=None):
    """Verify a frozen run and, unless `keep_unpublished`, ACCEPT it: point
    `published.json` at it and record the acceptance. Never touches the
    visible files -- a draft's visible files already are what acceptance
    keeps, so there is nothing to copy."""
    run_folder = resolve_output_path(run_folder)
    deal_root = resolve_output_path(deal_root)
    _reject_deal_alias(deal_root)
    borrower = os.path.basename(os.path.normpath(deal_root))
    owner = deal_root_of(run_folder)
    if owner is None or os.path.realpath(owner) != os.path.realpath(deal_root):
        raise ValueError(f"the run folder {run_folder!r} is not inside {deal_root!r}.")
    with store_lock(deal_root):
        data = read_published(deal_root)
        current_folder = (
            os.path.basename(os.path.normpath(run_folder)) == CURRENT_DIR
            and os.path.basename(os.path.dirname(os.path.normpath(run_folder))) == RECORD_DIR
        )
        if current_folder:
            if not period:
                raise ValueError("a period is required to recover selection from the current view.")
            current = _published_entry(data, period)
            try:
                immutable = _immutable_run_folder(deal_root, period, current)
            except ValueError as exc:
                raise ValueError(
                    f"published.json does not name a verified immutable run for {borrower} {period}.") from exc
            found = verified_run(current_dir(deal_root), period, current)
            checked = verify_manifest(found["folder"])
            run_folder = immutable
        else:
            owner = deal_root_of(run_folder)
            if owner is None or os.path.realpath(owner) != os.path.realpath(deal_root):
                raise ValueError(
                    f"the run folder {run_folder!r} is not inside {deal_root!r}.")
            checked = verify_manifest(run_folder)
            period = period or checked["period"] or os.path.basename(
                os.path.dirname(os.path.normpath(run_folder)))
            current = _published_entry(data, period)
            if checked["status"] != "ok":
                raise ValueError(
                    f"the run folder {run_folder!r} does not verify against its own manifest "
                    f"({checked['status']}).")
            verified_run(current_dir(deal_root), period,
                         os.path.basename(os.path.normpath(run_folder)))
        run_id = checked["run_id"] or os.path.basename(os.path.normpath(run_folder))

        if keep_unpublished:
            return {"published": False, "current": current, "run_id": run_id, "period": period}

        visible = read_manifest(record_dir(deal_root)) or {}
        if (visible.get("run_id") != run_id or visible.get("period") != period
                or visible.get("status") not in ("draft", "accepted")):
            raise ValueError("name the exact currently visible draft to accept; stale or discarded runs cannot be selected")
        if _discard_event(deal_root, borrower, period, run_id):
            raise ValueError("this draft was discarded; retry discard rather than accepting it")
        _guard_visible_edits(deal_root)
        _apply_writes(deal_root, _accept_writes(deal_root, run_id, period))

    return {
        "published": True,
        "current": run_id,
        "run_id": run_id,
        "period": period,
        "required_next_step": "refresh-dashboard",
    }


def finish_run(run_folder, deal_root, run_id=None, period=None, by=None,
               catalog=None, model_path=None, borrower_key=None, roles=None):
    run_folder = resolve_output_path(run_folder)
    deal_root = resolve_output_path(deal_root)
    _reject_deal_alias(deal_root)
    owner = deal_root_of(run_folder)
    if owner is None or os.path.realpath(owner) != os.path.realpath(deal_root):
        raise ValueError(
            f"the run folder {run_folder!r} is not inside {deal_root!r}.")
    if not os.path.isdir(run_folder):
        raise ValueError(f"there is no run folder at {run_folder!r}.")
    if not folder_files(run_folder, skip=(MANIFEST_NAME,)):
        raise ValueError(f"the run folder {run_folder!r} holds no files.")

    folder_run_id = os.path.basename(os.path.normpath(run_folder))
    month_dir = os.path.dirname(os.path.normpath(run_folder))
    folder_period = os.path.basename(month_dir)
    if os.path.basename(os.path.dirname(month_dir)) != RUNS_DIR:
        raise ValueError(f"the run folder {run_folder!r} is not under {RUNS_DIR}/<period>.")
    if run_id is not None and str(run_id) != folder_run_id:
        raise ValueError(
            f"run id {run_id!r} does not match folder {folder_run_id!r}.")
    if period is not None and str(period) != folder_period:
        raise ValueError(
            f"period {period!r} does not match folder {folder_period!r}.")
    run_id, period = folder_run_id, folder_period
    borrower = os.path.basename(os.path.normpath(deal_root))
    if borrower_key is not None and str(borrower_key) != borrower:
        raise ValueError(
            f"borrower key {borrower_key!r} does not match deal folder {borrower!r}.")

    ledger = read_ledger(run_folder)
    if ledger is None:
        raise ValueError(
            f"the run folder {run_folder!r} has no readable {LEDGER_NAME}.")
    waiting = pending_steps(ledger)
    if waiting:
        raise ValueError("the run is not complete: " + ", ".join(waiting))

    with store_lock(deal_root):
        ledger = read_ledger(run_folder)
        if (ledger is None or ledger.get("run_id") != run_id or ledger.get("period") != period):
            raise ValueError("the run ledger identity does not match its folder")
        if pending_steps(ledger):
            raise ValueError("the run is not complete: " + ", ".join(pending_steps(ledger)))
        visible = read_manifest(record_dir(deal_root)) or {}
        already = verify_manifest(run_folder)
        if already["status"] == "ok":
            if ledger.get("status") != "finished":
                raise ValueError("the frozen run ledger is not marked finished.")
            refreshed = {}
            if visible.get("run_id") != run_id or visible.get("period") != period:
                if any(e.get("event") == "discarded" and e.get("run_id") == run_id
                       and e.get("period") == period for e in _read_jsonl(_changelog_path(deal_root))):
                    raise ValueError("this run was discarded; start a new draft")
                writes = _delivery_writes(run_folder, deal_root)
                before = ledger.get("delivery_base")
                _guard_write_retry(deal_root, before, writes)
                _apply_writes(deal_root, writes)
                refreshed = {"written": list(writes)}
            else:
                _guard_visible_edits(deal_root)
            # Already delivered: never change an accepted run back to draft.
            return {
                "run_id": already["run_id"],
                "period": already["period"],
                "frozen": False,
                "adopted": None,
                "repointed": {},
                "refreshed": refreshed,
            }
        if already["status"] in ("mismatch", "unreadable"):
            raise ValueError(f"the run folder {run_folder!r} is not frozen cleanly.")

        accepted_edits = _finish_guard(deal_root, run_folder, ledger)
        _guard_stale_run(deal_root, run_id, period, ledger)
        adopted = adopt_into_run(run_folder, model_path)
        roles = roles or {
            "financial_workbook": deal_file_names(borrower, period)[0],
            "memo": deal_file_names(borrower, period)[1],
            "coverage": "coverage.json",
            "config": os.path.join("config", "borrower-config.json"),
        }
        repointed = repoint_refs(run_folder, deal_root, period, catalog, roles)
        ledger["status"] = "finished"
        write_output(ledger, ledger_path(run_folder))

        build_manifest(
            run_folder, run_id, period, by=by,
            borrower_key=borrower_key or borrower,
            roles=roles,
            status="draft",
            edit_intake=accepted_edits or None,
        )
        writes = _delivery_writes(run_folder, deal_root)
        # Refuse collisions with user files not named by the previous delivery.
        old_roles = (visible.get("roles") or {})
        for role in ("financial_workbook", "memo"):
            rel = roles[role]
            if rel not in old_roles.values() and transaction.path_in(deal_root, rel).exists():
                raise ValueError(f"delivery would overwrite an unrecorded file: {rel}")
        _guard_stale_run(deal_root, run_id, period, ledger)
        _apply_writes(deal_root, writes)
        refreshed = {"written": list(writes)}
    return {
        "run_id": run_id,
        "period": period,
        "frozen": True,
        "adopted": adopted,
        "repointed": repointed,
        "refreshed": refreshed,
    }


def _deliverable_check(deal_root):
    """Protect recorded working files, including settings, from silent replacement."""
    transaction.snapshot(deal_root)  # Validate paths before following any role.
    manifest = read_manifest(record_dir(deal_root))
    if manifest is None and Path(record_dir(deal_root), MANIFEST_NAME).exists():
        raise ValueError("the working manifest is damaged; repair it before changing files")
    return unapproved_deal_work(current_dir(deal_root))


def _guard_visible_edits(deal_root):
    """Block on anything not recorded, full stop.

    Used by accept and discard: an edit-intake acceptance is a review
    decision, never by itself a license for these operations to proceed --
    only a specific run pinned to consume that exact edit may (see
    `_finish_guard`). Accepting or discarding while a hand edit sits
    unconsumed would either mark an old draft accepted out from under a
    changed file, or erase the edit's context before it was ever computed.
    """
    found = _deliverable_check(deal_root)
    changed = found["changed"] + found["missing"] + found["unreadable"]
    if changed:
        raise ValueError(
            "the visible files were edited since the last delivery, so finishing "
            "this run would overwrite unrecorded work: " + ", ".join(changed))


def _guard_stale_run(deal_root, run_id, period, ledger):
    """Reject a late writer if any pinned working state changed, in any month."""
    if not isinstance(ledger.get("delivery_base"), dict):
        raise ValueError("this run has no pinned delivery base; start a new run through run_record open")
    if ledger["delivery_base"] != transaction.snapshot(deal_root):
        raise ValueError(
            "this run is stale: the working files or selected result changed since it opened. "
            "Preserve this run and start again against the current state.")


def _edit_intake_path(deal_root):
    return os.path.join(record_dir(deal_root), EDIT_INTAKE_NAME)


def read_edit_intake(deal_root):
    """This borrower's accepted-edit review record, or `{}` when none was ever
    taken. This is a REVIEW decision only -- it does not by itself exempt any
    operation. Each entry binds one exact compared file (`shown`, as `check`
    names it) to the byte digest it had at the moment it was accepted, so a
    further edit after acceptance no longer matches and is reported as
    changed again."""
    path = _edit_intake_path(deal_root)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if (not isinstance(data, dict) or data.get("schema") != EDIT_INTAKE_SCHEMA
            or not isinstance(data.get("accepted"), dict)):
        raise ValueError(f"{path} has an unsupported or malformed edit-intake record")
    return data


def _live_accepted(deal_root, manifest):
    """Accepted entries still bound to the run this borrower currently shows,
    i.e. taken before this exact manifest was superseded by a later delivery."""
    data = read_edit_intake(deal_root)
    if (not data or data.get("run_id") != manifest.get("run_id")
            or data.get("period") != manifest.get("period")):
        return {}
    return data.get("accepted") or {}


def unapproved_deal_work(monitor_output_folder, catalog=None):
    """What the deal folder holds that a delivery would write over.

    `changed` always lists every real content change, whether or not it was
    ever reviewed through edit-intake -- intake is a review decision, not an
    exemption, so accept/discard/an unrelated run's finish must keep seeing
    it here. `comparisons` exposes the bounded identity of each `changed`
    entry (the frozen digest it diverged from, its exact current digest, and
    -- when one exists -- the still-live intake acceptance for that exact
    digest) so a caller can require/recheck those exact digests rather than
    accept whatever now happens to be on disk.
    """
    empty = {"checked": False, "changed": [], "missing": [], "unreadable": [],
             "repackaged": [], "comparisons": [], "run_id": None, "period": None,
             "unpublished": False}
    deal = deal_root_of(resolve_output_path(monitor_output_folder))
    if deal is None:
        return empty
    manifest = read_manifest(record_dir(deal))
    if manifest is None or manifest.get("status") == "no_accepted_result":
        return empty

    period = str(manifest.get("period") or "")
    immutable = _immutable_run_folder(deal, period, manifest.get("run_id"))
    roles = validate_manifest_roles(immutable, manifest)
    workbook_name = roles["financial_workbook"]
    names = {workbook_name, roles["memo"]}
    live = _live_accepted(deal, manifest)
    changed, missing, unreadable, repackaged, comparisons = [], [], [], [], []
    for rel, expected in sorted((manifest.get("files") or {}).items()):
        if _zone_of(rel, names) == "deal":
            locations = [(os.path.join(deal, rel), rel)]
            if rel == workbook_name:
                locations.append((os.path.join(current_dir(deal), rel),
                                  f"{RECORD_DIR}/{CURRENT_DIR}/{rel}"))
        else:
            locations = [(os.path.join(current_dir(deal), *rel.split("/")),
                          f"{RECORD_DIR}/{CURRENT_DIR}/{rel}")]
        for here, shown in locations:
            if not os.path.exists(here):
                missing.append(shown)
                continue
            try:
                digest = file_sha256(here)
                if digest == expected:
                    continue
                # Bytes moved but cells may not have: Excel/SharePoint
                # re-wraps the container. Only a real content change
                # blocks delivery; repackaging is reported, not refused.
                frozen = os.path.join(immutable, *rel.split("/"))
                if integrity.same_content(frozen, here):
                    repackaged.append(shown)
                    continue
                changed.append(shown)
                taken = live.get(shown)
                comparisons.append({
                    "path": shown, "expected": expected, "digest": digest,
                    "intake": ({"digest": taken["digest"], "at": taken.get("at"),
                               "reason": taken.get("reason")}
                              if taken and taken.get("digest") == digest else None),
                })
            except (OSError, StoreAccessError):
                unreadable.append(shown)

    run_id = manifest.get("run_id")
    accepted_run = _published_entry(read_published(deal), period)
    return {
        "checked": True,
        "changed": changed,
        "missing": missing,
        "unreadable": unreadable,
        "repackaged": repackaged,
        "comparisons": comparisons,
        "run_id": run_id,
        "period": period,
        "unpublished": accepted_run != run_id,
    }


def record_edit_intake(deal_root, edits, reason=None):
    """Review specific hand-edited deliverables and record them as an
    intentional edit -- a decision only, not an exemption for any operation.

    `edits` is an iterable of `(path, digest)` pairs, named and hashed
    exactly as `check`'s `comparisons` entries report them (its `path` and
    `digest` fields) -- never a filename alone. The digest is REQUIRED and is
    rechecked here against the file's current bytes: if the file moved on
    again since the analyst reviewed it, this refuses rather than accept an
    edit nobody actually looked at. Only the financial workbook is a
    supported edit input; the memo is rendered fresh every run and is never
    read back as one, so a memo path is refused outright.

    Accepting a file here does not unblock delivery. It only makes the file
    eligible to be pinned as a new run's model input through
    `pin_consumed_edit`/`open --consume-edit`, and only that one run, with
    that exact still-matching digest, may then finish over it.
    """
    deal_root = resolve_output_path(deal_root)
    _reject_deal_alias(deal_root)
    if isinstance(edits, dict):
        edits = edits.items()
    pairs = sorted({(str(path), str(digest)) for path, digest in (edits or [])})
    if not pairs:
        raise ValueError(
            "name at least one changed file, with the exact digest `check` "
            "reported for it, to accept as an edit")
    with store_lock(deal_root):
        manifest = read_manifest(record_dir(deal_root))
        if manifest is None or manifest.get("status") == "no_accepted_result":
            raise ValueError(f"{deal_root!r} has no visible result to compare edits against")
        immutable = _immutable_run_folder(
            deal_root, str(manifest.get("period") or ""), manifest.get("run_id"))
        roles = validate_manifest_roles(immutable, manifest)
        supported = {roles["financial_workbook"],
                     f"{RECORD_DIR}/{CURRENT_DIR}/{roles['financial_workbook']}"}
        found = unapproved_deal_work(current_dir(deal_root))
        comparisons = {c["path"]: c for c in found["comparisons"]}
        at = _now().isoformat(timespec="seconds")
        data = read_edit_intake(deal_root)
        accepted = dict(data.get("accepted") or {}) if (
            data.get("run_id") == found["run_id"]
            and data.get("period") == found["period"]) else {}
        for path, digest in pairs:
            if path not in supported:
                raise ValueError(
                    f"{path!r} cannot be accepted through edit-intake: only the "
                    "financial workbook is a supported edit input, since it is "
                    "the only deliverable a run reads back in; the memo is "
                    "rendered fresh every run.")
            comparison = comparisons.get(path)
            if comparison is None:
                raise ValueError(
                    "only a file this check currently reports as changed can be "
                    f"accepted as an edit: {path!r}")
            if comparison["digest"] != digest:
                raise ValueError(
                    f"{path!r} no longer matches the digest just reviewed "
                    f"({digest}); check now reports {comparison['digest']}. Run "
                    "check again and accept its current comparison, not a stale one.")
            held = integrity._held_workbook_reason(
                os.path.join(immutable, roles["financial_workbook"]),
                os.path.join(deal_root, *path.split("/")))
            if held is not None:
                raise ValueError(
                    f"{path!r} cannot be taken as a reported figure: {held}. "
                    "This edit is held for clarification; only plain reported-figure "
                    "changes are eligible for edit-intake.")
            accepted[path] = {"digest": digest, "at": at, "reason": reason}
        record = {
            "schema": EDIT_INTAKE_SCHEMA, "schema_version": STORE_SCHEMA_VERSION,
            "period": found["period"], "run_id": found["run_id"], "accepted": accepted,
        }
        _apply_writes(deal_root, {".record/" + EDIT_INTAKE_NAME: _json_write(record)})
        remaining = unapproved_deal_work(current_dir(deal_root))
    return {
        "accepted": [path for path, _ in pairs],
        "period": found["period"],
        "run_id": found["run_id"],
        "still_blocking": remaining["changed"] + remaining["missing"] + remaining["unreadable"],
    }


def pin_consumed_edit(deal_root, path):
    """Verify `path` is a currently accepted, still-live edit to the
    financial workbook, and return the exact digest and filesystem source a
    NEW run must stage (through the existing `snapshot` path) as its model
    input to consume it. The caller pins this into that run's own ledger
    (`ledger["consume_edit"]`) -- only that run, checked against this exact
    digest, may later finish over the file; no other run and no accept or
    discard is exempted by it.
    """
    deal_root = resolve_output_path(deal_root)
    manifest = read_manifest(record_dir(deal_root))
    if manifest is None or manifest.get("status") == "no_accepted_result":
        raise ValueError(f"{deal_root!r} has no visible result to compare edits against")
    immutable = _immutable_run_folder(
        deal_root, str(manifest.get("period") or ""), manifest.get("run_id"))
    roles = validate_manifest_roles(immutable, manifest)
    supported = {roles["financial_workbook"],
                 f"{RECORD_DIR}/{CURRENT_DIR}/{roles['financial_workbook']}"}
    if path not in supported:
        raise ValueError(
            f"{path!r} cannot be consumed as an edit: only the financial "
            "workbook is a supported edit input; the memo is rendered fresh "
            "every run and is not read back as one.")
    found = unapproved_deal_work(current_dir(deal_root))
    comparisons = {c["path"]: c for c in found["comparisons"]}
    comparison = comparisons.get(path)
    if comparison is None:
        raise ValueError(f"{path!r} is not currently a changed file; run check again.")
    intake = comparison.get("intake")
    if intake is None:
        raise ValueError(
            f"{path!r} has not been accepted through edit-intake, or its accepted "
            "bytes no longer match its current bytes; run edit-intake again "
            "before consuming it in a new run.")
    return {"path": path, "digest": intake["digest"], "at": intake.get("at"),
            "reason": intake.get("reason"),
            "source_path": os.path.join(deal_root, *path.split("/"))}


def _finish_guard(deal_root, run_folder, ledger):
    """Block on anything not recorded -- except the one file THIS run pinned
    at open time to consume, and only while that pin still matches the live
    file, the live edit-intake acceptance for it, AND what this run actually
    staged as its own model input. The ledger pin alone is not evidence of
    consumption: a run that never actually snapshotted the accepted bytes
    (e.g. steps marked done directly, bypassing `snapshot`) must not be
    exempted just because its ledger claims to be pinned. No other run's
    finish, and no accept or discard, ever sees this exemption: it is read
    from this run's own ledger and this run's own staged file, not from any
    borrower-wide state.

    Returns the consumed-edit provenance to carry into this run's manifest.
    """
    found = _deliverable_check(deal_root)
    blocking = list(found["changed"]) + found["missing"] + found["unreadable"]
    pin = ledger.get("consume_edit")
    consumed = []
    if pin and pin.get("path") in blocking:
        comparisons = {c["path"]: c for c in found["comparisons"]}
        comparison = comparisons.get(pin["path"]) or {}
        intake = comparison.get("intake") or {}
        staged = os.path.join(run_folder, os.path.basename(pin["path"]))
        try:
            staged_digest = file_sha256(staged) if os.path.isfile(staged) else None
        except (OSError, StoreAccessError):
            staged_digest = None
        if (comparison.get("digest") == pin.get("digest")
                and intake.get("digest") == pin.get("digest")
                and staged_digest == pin.get("digest")):
            blocking.remove(pin["path"])
            consumed = [pin]
    if blocking:
        raise ValueError(
            "the visible files were edited since the last delivery, so finishing "
            "this run would overwrite unrecorded work: " + ", ".join(blocking))
    return consumed


def _latest_accepted_entry(deal_root):
    """The most recently accepted `{period, run}` for this borrower, across
    every period, or None when nothing has ever been accepted."""
    months = read_published(deal_root).get("months") or {}
    if not isinstance(months, dict) or not months:
        return None
    period = sorted(months)[-1]
    entry = months[period]
    run_id = entry.get("run") if isinstance(entry, dict) else None
    return {"period": period, "run": str(run_id)} if run_id else None


def _first_discard_writes(deal_root, manifest):
    writes = {}
    for role in ("financial_workbook", "memo"):
        rel = manifest["roles"][role]
        writes[rel] = None
        if role == "financial_workbook":
            writes[".record/current/" + rel] = None
    writes[".record/manifest.json"] = _json_write({
        "schema": MANIFEST_SCHEMA, "schema_version": STORE_SCHEMA_VERSION,
        "status": "no_accepted_result", "published": False,
        "period": manifest.get("period"), "files": {}, "roles": {},
    })
    return writes


def discard_draft(deal_root, run_id, period=None, reason=None):
    """Discard the currently visible DRAFT named `run_id` for `period`.

    Discarding never deletes the frozen run under `runs/<period>/<run-id>/` --
    only the working copy changes. When this borrower has an accepted run
    (for any period), the visible files are restored from it. Otherwise this
    is the borrower's first discard: the draft's deliverables are removed,
    `.record/manifest.json` is left naming no accepted result, and
    `.record/current/config` and `_model/` are untouched.
    """
    deal_root = resolve_output_path(deal_root)
    _reject_deal_alias(deal_root)
    borrower = _selection_borrower(deal_root)
    with store_lock(deal_root):
        manifest = read_manifest(record_dir(deal_root))
        decided = _discard_event(deal_root, borrower, period, run_id) if period else None
        if decided and manifest and manifest.get("status") in ("accepted", "no_accepted_result"):
            # A retry after the final manifest write has nothing left to change.
            _guard_visible_edits(deal_root)
            if manifest.get("status") == "accepted":
                published_run(current_dir(deal_root), manifest["period"], manifest["run_id"])
            return {"discarded": True, "run_id": run_id, "period": period,
                    "restored": manifest.get("run_id"), "status": manifest["status"]}
        if manifest is None or manifest.get("status") == "no_accepted_result":
            raise ValueError(f"{deal_root!r} has no visible draft to discard.")
        period = period or manifest.get("period")
        if (str(manifest.get("run_id")) != str(run_id)
                or str(manifest.get("period")) != str(period)):
            raise ValueError(
                f"{run_id!r} for {period!r} is not the currently visible result "
                f"({manifest.get('run_id')!r} for {manifest.get('period')!r}); "
                "name the exact result being discarded.")
        if manifest.get("status") == "accepted":
            raise ValueError(
                f"{run_id!r} for {period!r} is already accepted; there is no draft to discard.")
        if _latest_accepted_entry(deal_root) is None:
            raise ValueError(
                "the first review cannot be discarded: there is no prior accepted result to restore. "
                "Accept this draft or leave it unpublished.")

        decided = _discard_event(deal_root, borrower, period, run_id)
        if not decided:
            found = _deliverable_check(deal_root)
            changed = found["changed"] + found["missing"] + found["unreadable"]
            if changed:
                raise ValueError(
                    "the visible files were edited since this draft was delivered; "
                    "resolve those edits before discarding: " + ", ".join(changed))

        verified_run(current_dir(deal_root), period, run_id)
        accepted = _latest_accepted_entry(deal_root)
        restore_folder = (
            _immutable_run_folder(deal_root, accepted["period"], accepted["run"])
            if accepted else None)

        if accepted:
            published_run(current_dir(deal_root), accepted["period"], accepted["run"])
        # The decision line is recorded before the working files/manifest
        # change, and skipped here if a retry finds it already landed: a
        # partial discard interrupted after this line but before the rest
        # still shows the same visible draft above, so it can finish the
        # remaining writes below without appending the line twice.
        writes = {}
        if _discard_event(deal_root, borrower, period, run_id) is None:
            writes[".record/changelog.jsonl"] = _decision_write(deal_root, {
                "at": _now().isoformat(timespec="seconds"), "event": "discarded",
                "borrower": borrower, "period": period, "run_id": run_id, "reason": reason,
            })
        writes.update(_delivery_writes(restore_folder, deal_root, "accepted") if restore_folder
                      else _first_discard_writes(deal_root, manifest))
        if decided:
            # The recorded discard plus its saved draft identifies the pre-copy
            # files. Only those bytes or the verified restoration are allowed.
            before = transaction.snapshot(deal_root)
            roles = manifest["roles"]
            for rel, digest in manifest["files"].items():
                if rel in (roles["financial_workbook"], roles["memo"]):
                    before[rel] = digest
                if rel != roles["memo"]:
                    before[".record/current/" + rel] = digest
            _guard_write_retry(deal_root, before, writes)
        _apply_writes(deal_root, writes)

    return {
        "discarded": True,
        "run_id": run_id,
        "period": period,
        "restored": accepted["run"] if accepted else None,
        "status": "accepted" if accepted else "no_accepted_result",
    }
