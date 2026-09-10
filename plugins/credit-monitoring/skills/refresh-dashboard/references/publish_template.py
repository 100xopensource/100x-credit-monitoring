#!/usr/bin/env python3
"""Validate and copy the dashboard shell into an Artifact folder."""

import argparse
import os
from pathlib import Path
import shutil


SHARE_LIMIT = 1_000_000
PUBLISHED_AS = Path(".dashboard/credit-monitoring-portfolio.html")


def publish(source, out_dir):
    source = Path(source)
    out_dir = Path(out_dir)
    if not source.is_file():
        raise ValueError("no artifact file at %s" % source)
    if not out_dir.is_dir():
        raise ValueError("no Artifact folder at %s" % out_dir)

    text = source.read_text(encoding="utf-8")
    if "\x00" in text:
        raise ValueError("the artifact file holds a NUL byte")
    if not text.rstrip().endswith("</html>"):
        raise ValueError("the artifact file does not end with </html>")

    destination = out_dir / PUBLISHED_AS
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)
    return destination, len(text)


def main(argv=None):
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="the local Artifact folder")
    parser.add_argument("--file", default=here / "artifact-template.html")
    args = parser.parse_args(argv)
    try:
        destination, chars = publish(args.file, args.out)
    except ValueError as exc:
        parser.error(str(exc))
    print(destination)
    print("%d characters (%.1f%% of Cowork's measured share limit)" %
          (chars, 100.0 * chars / SHARE_LIMIT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
