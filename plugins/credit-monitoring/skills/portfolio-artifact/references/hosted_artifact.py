#!/usr/bin/env python3
"""Bake checked portfolio pages into the hosted dashboard template."""

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import tempfile

try:
    from plugin.lib.persistence import write_output
except ModuleNotFoundError:  # installed plugin archive has lib/ at its root
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from lib.persistence import write_output

INDEX_SCHEMA = "credit-monitor-artifact-index/v1"
SOURCE_BEGIN = "/* HOSTED_SOURCE_BEGIN */"
SOURCE_END = "/* HOSTED_SOURCE_END */"
CANDIDATE = ".dashboard/credit-monitoring-portfolio.baked.html"
_STATE_KEYS = {"artifact_id", "artifact_url", "title", "candidate_sha256"}
_NETWORK_API = re.compile(
    r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|serviceWorker)\b|"
    r"\bimport\s*\(|\b(?:import|export)\s+(?!\()|\b(?:new\s+)?Image\s*\(",
    re.IGNORECASE,
)
_LEGACY_BRIDGE = re.compile(
    r"\b(?:callMcpTool|mcp__|mcp_tools|create_artifact|update_artifact)\b|"
    r"window\s*(?:\.|\[\s*['\"])cowork\b",
    re.IGNORECASE,
)
_CSS_URL = re.compile(r"url\s*\((.*?)\)", re.IGNORECASE | re.DOTALL)


def _check_css(name, text):
    if re.search(r"@import\b", text, re.IGNORECASE):
        raise ValueError("%s contains a CSS import" % name)
    for match in _CSS_URL.finditer(text):
        target = match.group(1).strip().strip("'\"").strip().lower()
        if target and not target.startswith(("#", "data:")):
            raise ValueError("%s contains an external CSS resource" % name)


class _HostedHTMLGuard(HTMLParser):
    """Reject active dependencies while retaining ordinary outbound links."""

    _EMBEDS = {"iframe", "object", "embed", "applet"}
    _LINK_RELS = {"stylesheet", "preload", "prefetch", "import", "modulepreload"}
    _RESOURCE_ATTRS = {
        "audio": {"src"}, "image": {"href", "xlink:href"},
        "img": {"src", "srcset"}, "input": {"src"},
        "source": {"src", "srcset"}, "track": {"src"},
        "use": {"href", "xlink:href"}, "video": {"poster", "src"},
    }

    def __init__(self, name):
        super().__init__(convert_charrefs=True)
        self.name = name
        self.in_script = 0
        self.in_style = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = [(key.lower(), value or "") for key, value in attrs]
        values = dict(attrs)
        if (tag == "meta"
                and values.get("http-equiv", "").strip().lower() == "refresh"):
            raise ValueError("%s contains automatic navigation" % self.name)
        if tag == "base" and values.get("href", "").strip():
            raise ValueError("%s contains an external URL base" % self.name)
        if tag in self._EMBEDS:
            raise ValueError("%s contains embedded active content" % self.name)
        if any(key.startswith("on") for key, _value in attrs):
            raise ValueError("%s contains an inline event handler" % self.name)
        if values.get("style"):
            _check_css(self.name, values["style"])
        if tag == "script":
            if any(values.get(key, "").strip() for key in ("src", "href", "xlink:href")):
                raise ValueError("%s contains an external script" % self.name)
            script_type = values.get("type", "").split(";", 1)[0].strip().lower()
            if script_type not in {"application/json", "application/ld+json", "text/plain"}:
                self.in_script += 1
        elif tag == "style":
            self.in_style += 1
        elif tag == "link":
            rels = set(values.get("rel", "").lower().split())
            if rels & self._LINK_RELS and values.get("href", "").strip():
                raise ValueError("%s contains an external style or preload" % self.name)
        for attribute in self._RESOURCE_ATTRS.get(tag, ()):
            target = values.get(attribute, "").strip().lower()
            if target and (attribute == "srcset" or not target.startswith(("#", "data:"))):
                raise ValueError("%s contains an external media resource" % self.name)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self.in_script:
            self.in_script -= 1
        elif tag.lower() == "style" and self.in_style:
            self.in_style -= 1

    def handle_data(self, data):
        if self.in_script:
            if _NETWORK_API.search(data):
                raise ValueError("%s contains a script network API" % self.name)
            if _LEGACY_BRIDGE.search(data):
                raise ValueError("%s contains a legacy connector call" % self.name)
        if self.in_style:
            _check_css(self.name, data)


def _reject_active_html(name, text):
    parser = _HostedHTMLGuard(name)
    parser.feed(text)
    parser.close()


def read_state(path):
    """Read and validate a hosted artifact identity record."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"state": "absent"}
    except (OSError, UnicodeDecodeError, ValueError):
        return {"state": "malformed"}

    if (not isinstance(value, dict) or set(value) != _STATE_KEYS
            or not isinstance(value["title"], str) or not value["title"]
            or not isinstance(value["candidate_sha256"], str)
            or not value["candidate_sha256"]):
        return {"state": "malformed"}
    artifact_id = value["artifact_id"]
    artifact_url = value["artifact_url"]
    if artifact_id is None and artifact_url is None:
        return {"state": "pending", **value}
    if (isinstance(artifact_id, str) and artifact_id
            and isinstance(artifact_url, str)
            and artifact_url.startswith("https://")):
        return {"state": "complete", **value}
    return {"state": "malformed"}


def decide_action(path, candidate_sha256):
    """Choose the safe next hosted-artifact operation."""
    saved = read_state(path)
    if saved["state"] == "absent":
        return {"action": "create"}
    if saved["state"] != "complete":
        return {"action": "recover"}
    identity = {
        "artifact_id": saved["artifact_id"],
        "artifact_url": saved["artifact_url"],
    }
    action = "current" if saved["candidate_sha256"] == candidate_sha256 else "stale"
    return {"action": action, **identity}


def _pending(title, candidate_sha256):
    if (not isinstance(title, str) or not title
            or not isinstance(candidate_sha256, str) or not candidate_sha256):
        raise ValueError("title and candidate SHA-256 must be nonempty strings")
    return {
        "artifact_id": None,
        "artifact_url": None,
        "title": title,
        "candidate_sha256": candidate_sha256,
    }


def prepare_create(path, title, candidate_sha256):
    """Exclusively record first creation intent."""
    path = Path(path)
    if read_state(path)["state"] != "absent":
        raise ValueError("hosted artifact state already exists")
    pending = _pending(title, candidate_sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise ValueError("hosted artifact creation is already pending") from exc
    os.close(descriptor)
    write_output(pending, path)
    return pending


def record_identity(path, artifact_id, artifact_url, title, candidate_sha256):
    """Finalize first creation or replay an identical completed record."""
    path = Path(path)
    saved = read_state(path)
    if saved["state"] not in ("pending", "complete"):
        raise ValueError("hosted artifact state is not recordable")
    if (not isinstance(artifact_id, str) or not artifact_id
            or not isinstance(artifact_url, str) or not artifact_url.startswith("https://")
            or not isinstance(title, str) or not title
            or not isinstance(candidate_sha256, str) or not candidate_sha256):
        raise ValueError("hosted artifact identity fields are invalid")
    if saved["state"] == "pending":
        if title != saved["title"] or candidate_sha256 != saved["candidate_sha256"]:
            raise ValueError("hosted artifact creation does not match pending state")
    elif any((
            artifact_id != saved["artifact_id"], artifact_url != saved["artifact_url"],
            title != saved["title"], candidate_sha256 != saved["candidate_sha256"],
    )):
        raise ValueError("completed hosted artifact identity is immutable")
    complete = {
        "artifact_id": artifact_id,
        "artifact_url": artifact_url,
        "title": title,
        "candidate_sha256": candidate_sha256,
    }
    write_output(complete, path)
    return complete


def build_candidate(artifact, template, max_bytes=16_775_000):
    """Build one deterministic, self-contained hosted candidate."""
    artifact = Path(artifact)
    template = Path(template)
    if artifact.is_symlink() or not artifact.is_dir():
        raise ValueError("artifact root must be a real directory, not a symlink")
    artifact = artifact.resolve()

    def artifact_file(name):
        path = artifact / name
        if path.is_symlink():
            raise ValueError("index.json names a symlink: %s" % name)
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(artifact)
        except (OSError, ValueError) as exc:
            raise ValueError("index.json names a missing or external file: %s" % name) from exc
        if not resolved.is_file():
            raise ValueError("index.json does not name a regular file: %s" % name)
        return resolved

    try:
        index_text = artifact_file("index.json").read_text(encoding="utf-8")
        if "\0" in index_text:
            raise ValueError("index.json contains a NUL byte")
        index = json.loads(index_text)
    except (OSError, ValueError) as exc:
        raise ValueError("index.json is not readable JSON") from exc
    if (not isinstance(index, dict) or index.get("schema") != INDEX_SCHEMA
            or not isinstance(index.get("borrowers"), list)):
        raise ValueError("index.json is not %s" % INDEX_SCHEMA)

    def _add_files(files):
        for kind, name in files.items():
            if not kind.endswith("_url") and kind != "excel_name" and name:
                names.add(name)

    names = {"index.json"}
    included = []
    unavailable = []
    for row in index["borrowers"]:
        if not isinstance(row, dict) or not isinstance(row.get("files", {}), dict):
            raise ValueError("index.json has an invalid borrower row")
        borrower = str(row.get("name") or row.get("short") or row.get("folder") or "Unknown")
        if not row.get("readable"):
            unavailable.append(borrower)
            if row.get("files"):
                raise ValueError("an unavailable borrower must not name hosted files")
        else:
            included.append(borrower)
            _add_files(row.get("files", {}))

        # A draft preview and each prior accepted period read their own
        # files through the same internal reader, regardless of whether the
        # current period itself is readable.
        draft = row.get("draft")
        if draft is not None:
            if not isinstance(draft, dict) or not isinstance(draft.get("files", {}), dict):
                raise ValueError("index.json has an invalid draft row")
            _add_files(draft.get("files", {}))
        for hist in row.get("history") or []:
            if not isinstance(hist, dict) or not isinstance(hist.get("files", {}), dict):
                raise ValueError("index.json has an invalid history row")
            _add_files(hist.get("files", {}))
    if index.get("narrative"):
        names.add(index["narrative"])

    pages = {}
    for name in sorted(names):
        if (not isinstance(name, str) or Path(name).name != name
                or "/" in name or "\\" in name):
            raise ValueError("index.json names an unsafe file: %r" % name)
        path = artifact_file(name)
        try:
            pages[name] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError("index.json names an unreadable UTF-8 file: %s" % name) from exc
        if "\0" in pages[name]:
            raise ValueError("index.json names a file containing a NUL byte: %s" % name)
        if path.suffix.lower() == ".html":
            _reject_active_html(name, pages[name])

    data = json.dumps(pages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    data = data.replace("<", "\\u003c")
    source = """/* HOSTED_SOURCE_BEGIN */
window.PM = window.PM || {};
PM.embedded = %s;
PM.source = function () {
  return {
    readText: function (name) {
      if (!Object.prototype.hasOwnProperty.call(PM.embedded, name)) {
        var e = new Error('the hosted book does not hold this file: ' + name);
        e.kind = 'unavailable';
        return Promise.reject(e);
      }
      return Promise.resolve(PM.embedded[name]);
    },
    readJson: function (name) { return this.readText(name).then(JSON.parse); }
  };
};
/* HOSTED_SOURCE_END */""" % data

    html = template.read_text(encoding="utf-8")
    start = html.find(SOURCE_BEGIN)
    end = html.find(SOURCE_END)
    if start < 0 or end < start:
        raise ValueError("dashboard template has no hosted source markers")
    html = html[:start] + source + html[end + len(SOURCE_END):]
    html = re.sub(
        r'(?im)^\s*(?:<!doctype html>|</?html(?:\s[^>]*)?>|</?head>|</?body>)\s*$',
        "", html,
    )
    if "\0" in html:
        raise ValueError("dashboard template contains a NUL byte")
    payload = html.encode("utf-8")
    if len(payload) > max_bytes:
        raise ValueError("hosted candidate exceeds %d UTF-8 bytes" % max_bytes)

    dashboard = artifact / Path(CANDIDATE).parent
    if dashboard.is_symlink():
        raise ValueError("candidate output directory must not be a symlink")
    dashboard.mkdir(parents=True, exist_ok=True)
    if not dashboard.is_dir():
        raise ValueError("candidate output directory is not a directory")
    dashboard = dashboard.resolve()
    try:
        dashboard.relative_to(artifact)
    except ValueError as exc:
        raise ValueError("candidate output directory escapes the artifact root") from exc
    candidate = dashboard / Path(CANDIDATE).name
    if candidate.is_symlink():
        raise ValueError("candidate output must not be a symlink")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=candidate.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(payload)
        os.replace(temporary, candidate)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    return {
        "candidate": candidate,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "included_borrowers": included,
        "unavailable_borrowers": unavailable,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build")
    build.add_argument("--artifact", required=True)
    build.add_argument("--template", required=True)
    build.add_argument("--max-bytes", type=int, default=16_775_000)

    status = commands.add_parser("status")
    status.add_argument("--state", required=True)
    status.add_argument("--candidate-sha256", required=True)

    prepare = commands.add_parser("prepare-create")
    prepare.add_argument("--state", required=True)
    prepare.add_argument("--title", required=True)
    prepare.add_argument("--candidate-sha256", required=True)

    record = commands.add_parser("record")
    record.add_argument("--state", required=True)
    record.add_argument("--artifact-id", required=True)
    record.add_argument("--artifact-url", required=True)
    record.add_argument("--title", required=True)
    record.add_argument("--candidate-sha256", required=True)

    args = parser.parse_args(argv)
    if args.command == "build":
        result = build_candidate(args.artifact, args.template, args.max_bytes)
    elif args.command == "status":
        result = decide_action(args.state, args.candidate_sha256)
    elif args.command == "prepare-create":
        result = prepare_create(args.state, args.title, args.candidate_sha256)
    else:
        result = record_identity(
            args.state, args.artifact_id, args.artifact_url,
            args.title, args.candidate_sha256,
        )
    print(json.dumps({key: str(value) if isinstance(value, Path) else value
                      for key, value in result.items()}, sort_keys=True))


if __name__ == "__main__":
    main()
