#!/usr/bin/env python3
"""Deterministically build or verify credit-monitoring.plugin."""

import argparse
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "credit-monitoring.plugin"
# The plugin is one directory in this repository; the repository root is the
# marketplace root. Bundle members are named relative to PLUGIN, so the paths
# inside credit-monitoring.plugin are unchanged by where the plugin folder sits.
PLUGIN = ROOT / "plugins" / "credit-monitoring"
INCLUDED = (".claude-plugin", "agents", "config", "lib", "references", "skills")
FIXED = (1980, 1, 1, 0, 0, 0)


def members():
    yield "LICENSE", ROOT / "LICENSE"
    yield "NOTICE", ROOT / "NOTICE"
    yield "README.md", PLUGIN / "README.md"
    for directory in INCLUDED:
        for path in sorted((PLUGIN / directory).rglob("*")):
            name = path.relative_to(PLUGIN).as_posix()
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
                yield name, path


def build(path):
    with ZipFile(path, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for name, source in members():
            info = ZipInfo(name, FIXED)
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=9)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_hash():
    for line in (ROOT / "SHA256SUMS.txt").read_text().splitlines():
        value, name = line.split(None, 1)
        if name.strip().lstrip("*") == OUTPUT.name:
            return value
    raise SystemExit(f"missing {OUTPUT.name} in SHA256SUMS.txt")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "check"))
    args = parser.parse_args()
    if args.command == "build":
        build(OUTPUT)
        print(f"{digest(OUTPUT)}  {OUTPUT.name}")
        return
    candidate = OUTPUT.with_suffix(".plugin.check")
    try:
        build(candidate)
        candidate_hash = digest(candidate)
        if candidate_hash != expected_hash():
            raise SystemExit("deterministic rebuild does not match SHA256SUMS.txt")
        if OUTPUT.exists() and candidate.read_bytes() != OUTPUT.read_bytes():
            raise SystemExit("credit-monitoring.plugin differs from deterministic rebuild")
        print(f"OK {candidate_hash}  {OUTPUT.name} (deterministic rebuild matched published checksum)")
    finally:
        candidate.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
