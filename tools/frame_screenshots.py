#!/usr/bin/env python3
"""Frame raw demo screenshots to match the repository's illustration style.

The README mixes hand-drawn SVG illustrations (docs/assets/output-*.svg) with
captured screenshots of the deployed sample dashboard. Dropping a raw capture
next to those illustrations reads as a different document: no frame, no
rounding, and whatever height the capture happened to be.

This reframes each capture the way the SVGs are built -- a #F4F3F8 canvas with
rounded corners, holding a rounded card outlined in #E6E5ED -- and pads every
result to one canvas size so the README renders them identically.

Captures are scaled to a common width, never cropped or stretched: the small
height differences between them are absorbed by the frame, not by the image.

Pillow is required to run this, and is deliberately not in requirements.txt --
regenerating these images is a maintainer chore, not part of using the plugin.

    python3 -m pip install Pillow
    python3 tools/frame_screenshots.py <source-dir>

Source files are matched to their framed counterparts in docs/assets/ by name.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"

CANVAS_FILL = (244, 243, 248)  # #F4F3F8, the SVG illustrations' backdrop
CARD_STROKE = (230, 229, 237)  # #E6E5ED, their card outline
CARD_WIDTH = 1040
PADDING = 44
CANVAS_RADIUS = 28
CARD_RADIUS = 16
SUPERSAMPLE = 4  # corners are masked at this scale, then averaged down


def rounded_mask(size, radius):
    """An antialiased mask for a rounded rectangle of the given size."""
    width, height = size
    big = Image.new("L", (width * SUPERSAMPLE, height * SUPERSAMPLE), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, width * SUPERSAMPLE - 1, height * SUPERSAMPLE - 1),
        radius=radius * SUPERSAMPLE,
        fill=255,
    )
    return big.resize(size, Image.LANCZOS)


def scaled(path):
    """The capture at CARD_WIDTH, aspect preserved."""
    image = Image.open(path).convert("RGB")
    width, height = image.size
    return image.resize(
        (CARD_WIDTH, round(height * CARD_WIDTH / width)), Image.LANCZOS
    )


def frame(card, canvas_size):
    """One capture, centred in the canvas the whole set shares."""
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    backdrop = Image.new("RGBA", canvas_size, CANVAS_FILL + (255,))
    canvas.paste(backdrop, (0, 0), rounded_mask(canvas_size, CANVAS_RADIUS))

    top = (canvas_size[1] - card.height) // 2
    canvas.paste(card, (PADDING, top), rounded_mask(card.size, CARD_RADIUS))

    # Drawn after the paste so the outline sits on the capture's own edge,
    # the way the SVG cards are stroked rather than inset.
    outline = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    ImageDraw.Draw(outline).rounded_rectangle(
        (PADDING, top, PADDING + card.width - 1, top + card.height - 1),
        radius=CARD_RADIUS,
        outline=CARD_STROKE + (255,),
        width=1,
    )
    return Image.alpha_composite(canvas, outline)


def main(source):
    sources = sorted(Path(source).glob("screenshot-*.png"))
    if not sources:
        raise SystemExit(f"no screenshot-*.png found in {source}")

    cards = {path.name: scaled(path) for path in sources}
    # One canvas for the whole set, sized to the tallest capture so none is
    # cropped. Shorter ones simply carry a little more frame.
    canvas_size = (
        CARD_WIDTH + PADDING * 2,
        max(card.height for card in cards.values()) + PADDING * 2,
    )

    for name, card in cards.items():
        destination = ASSETS / name
        frame(card, canvas_size).save(destination, optimize=True)
        print(f"{destination.relative_to(ROOT)}  {canvas_size[0]}x{canvas_size[1]}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
