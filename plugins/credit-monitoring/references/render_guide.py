#!/usr/bin/env python3
"""Render a self-contained copy of the folder-setup handout by inlining its screenshots.

Keeps base64 out of the committed repo: the committed HTML references its PNGs relatively; this
inlines them as data URIs at delivery time, so the analyst gets one file that renders its images
anywhere. It's a help doc handed to the analyst, not a monitoring output.

Usage:
    python render_guide.py              # render to a scratch temp file, print its path (recommended)
    python render_guide.py <out.html>   # render to a chosen path (may not be the committed template)

Either way it prints the absolute path of the self-contained copy to attach — never the committed
source template (which keeps relative image src's and would show broken images on its own).
"""
import base64
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "getting-your-folders-ready.html")


def inline(html):
    """Replace every relative src="<name>.png|jpg" with a base64 data URI."""
    def repl(match):
        name = match.group(1)
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            return match.group(0)
        with open(path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode("ascii")
        ext = os.path.splitext(name)[1].lstrip(".").lower()
        media = "jpeg" if ext in ("jpg", "jpeg") else "png"
        return 'src="data:image/{};base64,{}"'.format(media, data)

    return re.sub(r'src="([^":]+\.(?:png|jpe?g))"', repl, html)


def main():
    with open(TEMPLATE, encoding="utf-8") as fh:
        html = inline(fh.read())

    if len(sys.argv) > 1:
        dest = os.path.abspath(sys.argv[1])
        if dest == TEMPLATE:
            sys.exit("refusing to overwrite the source template — pick a different output path")
    else:
        fd, dest = tempfile.mkstemp(prefix="folder-setup-guide-", suffix=".html")
        os.close(fd)

    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(dest)


if __name__ == "__main__":
    main()
