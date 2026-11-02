#!/usr/bin/env python3
"""Read and write one SharePoint folder over Microsoft Graph.

For a run with no local folders: pull the files the run needs into a work
area, run the engines there, and save the outputs back.

  sharepoint.py --site SITE --folder SOURCE download --into WORK/source PATH...
  sharepoint.py --site SITE --folder STORE download --into WORK/store --record WORK/read.json PATH...
  sharepoint.py --site SITE --folder SOURCE url
  CREDIT_MONITOR_SOURCE_ROOT=WORK/source CREDIT_MONITOR_STORE_ROOT=WORK/store <engine>
  sharepoint.py --site SITE --folder STORE upload --from WORK/store --changed-since WORK/read.json PATH...

SITE is `<host>:/sites/<name>` or `<host>:/teams/<name>`. A folder is its
path from the root of the site's document library. A PATH is relative to that folder, the part of a
`source:` or `store:` reference after the colon, and it lands at the same path
under the work area. A PATH that names a folder takes every file below it,
except the temporary files the store writes on the way to a finished one
(`.store-write-*`, `tmp*`, `*.tmp`): a crash can leave one behind, and the
channel can't delete it once it is saved. Download only the files the run
reads, and upload only the files it wrote. Each command prints JSON. A failure
exits 1 and names the request and what the server said.

`where` prints the site id, the drive id and the folder path, for a page that
links to the saved files. `url` prints the folder's own web address as one
plain line, not JSON, so a shell can export it as CREDIT_MONITOR_SOURCE_URL or
CREDIT_MONITOR_STORE_URL; the engines build a memo's file links from it. An upload is read back and compared before it
counts: a workbook by its cells, because the library rewrites a workbook on
save; every other file byte for byte. Each saved file's line, and each file
in a listing, carries its `web_url`, the link that opens it in the browser,
when the library gives one. An upload leaves an unchanged file alone and gives
no line for it, so take the link of a file to hand over from a listing.

`--record` keeps what a download read: each file's SHA-256 and the time the
library last saved it. `upload --changed-since` that record saves only the
files that are new or differ from it, and nothing at all when a file it would
replace was saved in the library since it was read. Each of its saves is
conditional: a file it replaces goes with `If-Match` on the copy it checked,
and a new one only while the library has none, so a save that lands in
between stops the upload at that file. Each save updates the record, its
save time at once and its SHA-256 once read back, so a later upload, a retry
included, sends only what changed after it. An upload saves
the files that point at others last: a borrower's `published.json` and
`changelog.jsonl`, then its `manifest.json`, then the dashboard's `index.json`.

The helper sends GET, POST and PUT only. It never deletes or renames: a file
removed from the work area stays in SharePoint. It builds no `Authorization`
header; the connection adds the credential on its way out. Downloads and
upload sessions leave Graph for the tenant's SharePoint host. A 403, or a proxy
that refuses a host, is the connection refusing a host, a method or a path: the
helper names them and does not retry, because only an admin can change that
setting. A server error, a dropped connection or a 429 gets one more try, after
the wait the server asks for in `Retry-After`, up to a minute. It goes through
the proxy in `HTTPS_PROXY` and trusts the CA bundle in `REQUESTS_CA_BUNDLE` or
`SSL_CERT_FILE`, else the system's.
"""

import argparse
import hashlib
import http.client
import json
import os
import re
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from plugin.lib import workbook_integrity
except ModuleNotFoundError:
    import workbook_integrity  # noqa: E402 - extracted plugin script

GRAPH = "https://graph.microsoft.com/v1.0"
# Graph takes a file up to 4 MB in one PUT; a larger one goes through an
# upload session, in chunks that are multiples of 320 KiB.
ONE_PUT_LIMIT = 4 * 1024 * 1024
CHUNK = 16 * 320 * 1024
ADMIN_SETTING = ("A refusal naming a host, a method or a path is a connection setting "
                 "only an admin can change.")
# Files the store writes on the way to a finished one, never results.
TEMPORARY = re.compile(r"^(\.store-write-|tmp)|\.tmp$")
# The longest wait a Retry-After gets before the second try, and the wait without one.
MAX_WAIT, WAIT = 60.0, 2.0


class GraphError(Exception):
    """A Graph request failed. The message names the request and what the server said."""


class RefusedByConnection(GraphError):
    """The connection refused a host, a method or a path."""


class UntrustedCertificate(GraphError):
    """The server's certificate is not signed by a CA this process trusts."""


class _Conflict(GraphError):
    """A conditional save found another copy in the library: a 412 or a 409."""


class ReadBackMismatch(Exception):
    """A file read back from SharePoint is not the file that was saved."""


class ChangedInSharePoint(Exception):
    """A file an upload would replace was saved in the library since it was read."""


class Folder:
    """One folder in a SharePoint site's document library.

    `site` is `<host>:/sites/<name>` or `<host>:/teams/<name>`; `folder` is
    the folder's path from the library's root. The site id is looked up on
    first use.
    """

    def __init__(self, site, folder, graph=None):
        self.site = site
        self.folder = folder.strip("/")
        self.graph = (graph or GRAPH).rstrip("/")
        self._site_id = None
        ca = next((path for path in (os.environ.get("REQUESTS_CA_BUNDLE"),
                                     os.environ.get("SSL_CERT_FILE"))
                   if path and os.path.isfile(path)), None)
        self._redirects = _Redirects()
        self._opener = urllib.request.build_opener(
            self._redirects,
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)))

    def where(self):
        """`{site, site_id, drive_id, folder}`."""
        drive = json.loads(self._get(f"{self.graph}/sites/{self.site_id()}/drive"))
        return {"site": self.site, "site_id": self.site_id(), "drive_id": drive["id"],
                "folder": self.folder}

    def web_url(self):
        """The address that opens the folder in the browser, or "" when the library gives none."""
        return json.loads(self._get(self._item_url(""))).get("webUrl") or ""

    def site_id(self):
        if self._site_id is None:
            site = urllib.parse.quote(self.site, safe=":/")
            self._site_id = json.loads(self._get(f"{self.graph}/sites/{site}"))["id"]
        return self._site_id

    def list(self, path=""):
        """`[{name, folder, size, modified, web_url}]` for one folder level, every page."""
        url = self._item_url(_inside_or_raise(path)) + "/children?$top=200"
        out = []
        while url:
            page = json.loads(self._get(url))
            for item in page.get("value") or []:
                entry = {"name": item["name"], "folder": "folder" in item,
                         "size": None if "folder" in item else item.get("size"),
                         "modified": item.get("lastModifiedDateTime")}
                if item.get("webUrl"):
                    entry["web_url"] = item["webUrl"]
                out.append(entry)
            url = page.get("@odata.nextLink")
        return out

    def download(self, paths, into, record=None):
        """Each path's file, or every file below a folder, to `into/<path>`. `[{path, bytes}]`.

        With `record`, also keep each file's SHA-256 and last save time in that JSON file.
        """
        out, read = [], {}
        for path in map(_inside_or_raise, paths):
            item = json.loads(self._get(self._item_url(path)))
            files = (self._files_under(path) if "folder" in item
                     else [(path, item.get("lastModifiedDateTime"))])
            for rel, modified in files:
                body = self._get(self._content_url(rel))
                dest = _local_path(into, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(body)
                out.append({"path": rel, "bytes": len(body)})
                read[rel] = {"sha256": hashlib.sha256(body).hexdigest(), "modified": modified}
        if record:
            _write_record(record, {**_read_record(record), **read})
        return out

    def upload(self, paths, from_dir, changed_since=None):
        """Each `from_dir/<path>`, or every file below a folder, to the folder's path,
        read back and compared. `[{path, bytes, web_url}]`.

        With `changed_since`, a record `download` kept, only new and changed files go,
        and none at all when one of them was saved in the library since it was read.
        Raises ChangedInSharePoint then, and ReadBackMismatch when the copy in
        SharePoint is not the file sent.
        """
        files = [rel for path in paths for rel in _files_below(from_dir, _inside_or_raise(path))]
        read, etags = None, {}
        if changed_since:
            read = _read_record(changed_since)
            files = [rel for rel in files
                     if _sha256(_local_path(from_dir, rel)) != read.get(rel, {}).get("sha256")]
            moved = []
            for rel in files:
                if rel in read:
                    item = json.loads(self._get(self._item_url(rel)))
                    if item.get("lastModifiedDateTime") != read[rel]["modified"]:
                        moved.append(rel)
                    etags[rel] = item.get("eTag")
            if moved:
                raise ChangedInSharePoint(
                    "saved in SharePoint since this run read it, so nothing was uploaded: "
                    + ", ".join(moved))
        out = []
        for path in sorted(files, key=_save_order):
            local = _local_path(from_dir, path)
            with open(local, "rb") as fh:
                body = fh.read()
            try:
                # With a record, a save replaces only the copy checked above, and a file the
                # record lacks goes only while SharePoint has none.
                item = self._save(path, body, etags.get(path),
                                  new=read is not None and path not in read)
            except _Conflict:
                raise ChangedInSharePoint(
                    "saved in SharePoint since this run read it, so it and the files after it "
                    f"were not uploaded: {path}"
                    + (f" ({len(out)} files before it were)" if out else "")) from None
            if read is not None:
                # The save time goes in first, so a retry after a failed read-back
                # doesn't take this run's own save for someone else's.
                read[path] = {**read.get(path, {}), "modified": item.get("lastModifiedDateTime")}
                _write_record(changed_since, read)
            if not _same_as_sent(local, self._get(self._content_url(path))):
                raise ReadBackMismatch(f"{path}: the copy read back from SharePoint "
                                       "is not the file sent")
            saved = {"path": path, "bytes": len(body)}
            if item.get("webUrl"):
                saved["web_url"] = item["webUrl"]
            out.append(saved)
            if read is not None:
                read[path]["sha256"] = hashlib.sha256(body).hexdigest()
                _write_record(changed_since, read)
        return out

    def _call(self, method, url, body=None, headers=None):
        """One request. A server error, a 429 or a dropped connection gets one more try,
        a reply after the wait it asks for; a refusal or an untrusted certificate gets
        none, since a retry cannot change it."""
        request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        for attempt in (1, 2):
            self._redirects.target = None
            try:
                with self._opener.open(request, timeout=300) as reply:
                    return reply.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 403:
                    raise RefusedByConnection(
                        f"{self._answered(method, url)} was refused: {_server_message(exc)}. "
                        + ADMIN_SETTING) from None
                if exc.code in (409, 412):
                    raise _Conflict(f"{self._answered(method, url)} answered {exc.code}: "
                                    f"{_server_message(exc)}") from None
                if (exc.code < 500 and exc.code != 429) or attempt == 2:
                    raise GraphError(f"{self._answered(method, url)} answered {exc.code}: "
                                     f"{_server_message(exc)}") from None
                time.sleep(_wait(exc.headers.get("Retry-After")))
            except (OSError, http.client.HTTPException) as exc:
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, ssl.SSLCertVerificationError):
                    raise UntrustedCertificate(
                        f"{self._answered(method, url)} failed: its certificate is not trusted "
                        f"({reason.verify_message}). To trust the proxy, set SSL_CERT_FILE to "
                        "the proxy's CA bundle.") from None
                if str(reason).startswith("Tunnel connection failed: 403"):
                    host = urllib.parse.urlsplit(self._redirects.target or url).netloc
                    raise RefusedByConnection(
                        f"CONNECT {host} was refused by the proxy, for "
                        f"{_method_host_path(method, url)}. {ADMIN_SETTING}") from None
                if attempt == 2:
                    raise GraphError(f"{_method_host_path(method, url)} failed: {exc}") from None

    def _answered(self, method, url):
        """The request that failed. A download fails at the SharePoint host it redirects to."""
        if self._redirects.target is None:
            return _method_host_path(method, url)
        return (f"{_method_host_path(method, self._redirects.target)} "
                f"(redirected from {_method_host_path(method, url)})")

    def _get(self, url):
        return self._call("GET", url)

    def _item_url(self, path):
        # Graph refuses a site path and an item path in one URL, so the site id comes first.
        full = urllib.parse.quote(f"{self.folder}/{path}".strip("/"), safe="/")
        return f"{self.graph}/sites/{self.site_id()}/drive/root:/{full}:"

    def _content_url(self, path):
        return self._item_url(path) + "/content"

    def _files_under(self, path):
        """`[(path, last save time)]` for every file below a folder."""
        out = []
        for entry in self.list(path):
            inside = f"{path}/{entry['name']}"
            out += self._files_under(inside) if entry["folder"] else [(inside, entry["modified"])]
        return out

    def _save(self, path, body, etag=None, new=False):
        """Save one file. The library's item for it, or {} when it answers without one.

        With `etag`, only over that copy; with `new`, only when the library has no file
        there. Raises _Conflict otherwise."""
        only_if = {"If-Match": etag} if etag else {}
        if len(body) <= ONE_PUT_LIMIT:
            reply = self._call("PUT", self._content_url(path)
                               + ("?@microsoft.graph.conflictBehavior=fail" if new else ""),
                               body, {"Content-Type": "application/octet-stream", **only_if})
        else:
            behavior = json.dumps({"item": {
                "@microsoft.graph.conflictBehavior": "fail" if new else "replace"}}).encode()
            session = json.loads(self._call("POST", self._item_url(path) + "/createUploadSession",
                                            behavior,
                                            {"Content-Type": "application/json", **only_if}))
            for start in range(0, len(body), CHUNK):
                chunk = body[start:start + CHUNK]
                reply = self._call(
                    "PUT", session["uploadUrl"], chunk,
                    {"Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(body)}"})
        try:
            return json.loads(reply)
        except ValueError:
            return {}


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect and keeps where it went, so an error names the host that answered."""

    target = None

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.target = newurl
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _inside_or_raise(path):
    """`path` as a path below the folder. Raises ValueError when it climbs out."""
    parts = [part for part in str(path).split("/") if part not in ("", ".")]
    if ".." in parts:
        raise ValueError(f"{path}: a path must stay inside the folder")
    return "/".join(parts)


def _local_path(root, rel):
    return os.path.join(root, *rel.split("/"))


def _files_below(root, path):
    """`[path]`, or every file below `root/<path>` when it is a folder, as paths under `root`,
    leaving out temporary files. Raises ValueError when `path` names one."""
    top = _local_path(root, path)
    if not os.path.isdir(top):
        if TEMPORARY.search(os.path.basename(top)):
            raise ValueError(f"{path}: a temporary file is never uploaded")
        return [path]
    return sorted(os.path.relpath(os.path.join(here, name), root).replace(os.sep, "/")
                  for here, _, names in os.walk(top) for name in names
                  if not TEMPORARY.search(name))


def _save_order(path):
    """Files that point at others go last, so a reader never finds a pointer to a file
    not saved yet: a borrower's published.json and changelog, its manifest, the index."""
    if path.rsplit("/", 1)[-1] == "index.json":
        return 3
    if path.endswith("/.record/manifest.json"):
        return 2
    if path.endswith(("/.record/published.json", "/.record/changelog.jsonl")):
        return 1
    return 0


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _read_record(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def _write_record(path, read):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(read, fh, indent=1, sort_keys=True)


def _same_as_sent(local, back):
    """Whether `back`, read back from the library, is the file at `local`: a workbook by its
    cells, since the library rewrites a workbook on save, and any other file byte for byte."""
    if os.path.splitext(local)[1].lower() not in (".xlsx", ".xlsm"):
        with open(local, "rb") as fh:
            return fh.read() == back
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "read-back" + os.path.splitext(local)[1])
        with open(copy, "wb") as fh:
            fh.write(back)
        return workbook_integrity.same_workbook_content(local, copy)


def _wait(retry_after):
    """Seconds to wait before the second try: the server's Retry-After, up to MAX_WAIT."""
    try:
        return min(MAX_WAIT, max(0.0, float(retry_after)))
    except (TypeError, ValueError):
        return WAIT


def _method_host_path(method, url):
    """`METHOD host/path`. The query stays out: an upload session's carries its token."""
    parts = urllib.parse.urlsplit(url)
    return f"{method} {parts.netloc}{urllib.parse.unquote(parts.path)}"


def _server_message(exc):
    """Graph's own message from an error reply, or the start of the reply's body."""
    body = exc.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return body[:300] or exc.reason


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="sharepoint.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", required=True,
                        help="the SharePoint site, <host>:/sites/<name> or <host>:/teams/<name>")
    parser.add_argument("--folder", required=True,
                        help="the folder's path from the root of the site's document library")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("where", help="print the site id, the drive id and the folder path")
    commands.add_parser("url", help="print the folder's web address as one plain line")
    listing = commands.add_parser("list", help="print the files and folders one folder holds")
    listing.add_argument("path", nargs="?", default="", metavar="PATH",
                         help="a folder below the folder; the folder itself when left out")
    down = commands.add_parser("download", help="copy files from the folder to the work area")
    down.add_argument("--into", required=True, metavar="DIR", help="the work area")
    down.add_argument("--record", metavar="FILE",
                      help="keep each file's SHA-256 and last save time in this JSON file")
    down.add_argument("paths", nargs="+", metavar="PATH", help="a file or a folder below the folder")
    up = commands.add_parser("upload", help="save files from the work area to the folder")
    up.add_argument("--from", dest="from_dir", required=True, metavar="DIR", help="the work area")
    up.add_argument("--changed-since", metavar="FILE",
                    help="a download's --record: save only new and changed files, and nothing "
                         "when one was saved in the library since it was read")
    up.add_argument("paths", nargs="+", metavar="PATH", help="a file or a folder below the work area")
    args = parser.parse_args(argv)

    folder = Folder(args.site, args.folder)
    try:
        if args.command == "url":
            print(folder.web_url())
            return 0
        if args.command == "where":
            result = folder.where()
        elif args.command == "list":
            result = folder.list(args.path)
        elif args.command == "download":
            result = folder.download(args.paths, args.into, args.record)
        else:
            result = folder.upload(args.paths, args.from_dir, args.changed_since)
    except (GraphError, ReadBackMismatch, ChangedInSharePoint, OSError, ValueError) as exc:
        print(f"sharepoint.py: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
