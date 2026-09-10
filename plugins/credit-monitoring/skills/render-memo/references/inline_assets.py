#!/usr/bin/env python3
"""Neutral memo post-processor.

This package ships no inline asset substitution step, so this script just checks for stale
`{{CA_LOGO}}` tokens and otherwise leaves the file unchanged.
"""

import sys


def main():
    args = sys.argv[1:]
    check_only = False
    if args and args[0] == "--check":
        check_only, args = True, args[1:]
    if not args:
        print("usage: python inline_assets.py [--check] <memo.html> [more.html ...]")
        sys.exit(2)

    failed = False
    for path in args:
        with open(path, encoding="utf-8") as fh:
            html = fh.read()
        if "{{CA_LOGO}}" in html:
            failed = True
            print(f"MISSING {path}: stale asset token still present")
        elif check_only:
            print(f"OK {path}: no asset token present")
        else:
            print(f"OK {path}: left unchanged")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
