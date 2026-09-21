"""The soft background behind the message pane.

Tk cannot blur anything and cannot alpha-composite canvas items. Its only
translucency is `stipple`, which is a dot screen and looks like one. So the
soft shapes are not drawn -- they are *computed*, pixel by pixel, into a
bitmap that Tk is then handed whole. Everything above it is opaque, which is
the constraint that shaped all of this.

No Pillow and no numpy: a PPM is a three-line header followed by raw RGB
bytes, and `tk.PhotoImage` reads one directly.

The cost is per pixel, so the inner loop matters. A full-size pane takes
roughly a third of a second, which is fine once and far too slow to repeat
while somebody drags a window edge -- hence the cache, and the debounce in
the caller.
"""

from __future__ import annotations

import math
import tkinter as tk

from im.client.view.tk import theme as t

#: Sizes are rounded to this before rendering, so nudging a window edge by a
#: pixel does not throw away a perfectly good image.
QUANTUM = 40

#: How far the decorative blobs push the base colour. Low on purpose: this is
#: meant to be noticed only if you look for it.
STRENGTH = 0.30

#: Faint dot motif. Spacing in device pixels, and how far it darkens.
DOT_SPACING = 26
DOT_STRENGTH = 0.55

_cache: dict[tuple[int, int], tk.PhotoImage] = {}


def _rgb(colour: str) -> tuple[int, int, int]:
    return (int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16))


def _blobs(width: int, height: int) -> list[tuple[float, float, float, tuple[int, int, int]]]:
    """Where the out-of-focus colour comes from.

    Placed relative to the pane so the composition survives a resize, and
    deliberately pushed off the edges -- a blob you can see the whole of reads
    as a circle someone drew, not as light.
    """
    return [
        (width * 0.08, height * 0.12, max(width, height) * 0.55, _rgb(t.ORANGE)),
        (width * 0.95, height * 0.30, max(width, height) * 0.45, _rgb(t.PEACH)),
        (width * 0.55, height * 1.02, max(width, height) * 0.60, _rgb(t.ORANGE_DEEP)),
    ]


def _render(width: int, height: int) -> tk.PhotoImage:
    top = _rgb(t.PAGE)
    bottom = _rgb(t.CREAM)  # the canvas background, so the image ends where the canvas begins
    spots = _blobs(width, height)

    # Per blob: the column range it can touch, and dx squared for each column
    # in it. Computing this once per blob rather than once per pixel is what
    # makes a full pane a third of a second instead of several.
    prepared = []
    for cx, cy, radius, colour in spots:
        left = max(0, int(cx - radius))
        right = min(width, int(cx + radius) + 1)
        if left < right:
            prepared.append((cx, cy, radius, colour, left, right,
                             [(x - cx) ** 2 for x in range(left, right)]))

    rows: list[bytes] = []
    for y in range(height):
        f = y / max(1, height - 1)
        base = (
            top[0] + (bottom[0] - top[0]) * f,
            top[1] + (bottom[1] - top[1]) * f,
            top[2] + (bottom[2] - top[2]) * f,
        )
        # Start from a row of flat gradient and patch only where a blob
        # reaches. Most pixels are never touched twice.
        row = bytearray(bytes((int(base[0]), int(base[1]), int(base[2]))) * width)

        for cx, cy, radius, colour, left, right, dxs in prepared:
            dy2 = (y - cy) ** 2
            if dy2 > radius * radius:
                continue
            reach = math.sqrt(radius * radius - dy2)
            x0 = max(left, int(cx - reach))
            x1 = min(right, int(cx + reach) + 1)
            for x in range(x0, x1):
                d2 = dxs[x - left] + dy2
                if d2 >= radius * radius:
                    continue
                # Squared falloff, so the edge fades out rather than stopping.
                a = (1.0 - math.sqrt(d2) / radius) ** 2 * STRENGTH
                i = x * 3
                row[i] = int(row[i] + (colour[0] - row[i]) * a)
                row[i + 1] = int(row[i + 1] + (colour[1] - row[i + 1]) * a)
                row[i + 2] = int(row[i + 2] + (colour[2] - row[i + 2]) * a)

        # The motif, baked in for the same reason the blobs are: drawn on top
        # it would have to be opaque, and an opaque dot is a speck of dirt.
        if y % DOT_SPACING == 0:
            for x in range(0, width, DOT_SPACING):
                i = x * 3
                row[i] = int(row[i] - DOT_STRENGTH * 8)
                row[i + 1] = int(row[i + 1] - DOT_STRENGTH * 10)
                row[i + 2] = int(row[i + 2] - DOT_STRENGTH * 14)

        rows.append(bytes(row))

    header = b"P6\n%d %d\n255\n" % (width, height)
    return tk.PhotoImage(data=header + b"".join(rows), format="ppm")


def backdrop(width: int, height: int) -> tk.PhotoImage | None:
    """A background image for a pane this size, cached.

    None for a pane too small to be worth it, which is what a canvas reports
    before it has been laid out.
    """
    if width < 40 or height < 40:
        return None

    width = max(QUANTUM, (width // QUANTUM + 1) * QUANTUM)
    height = max(QUANTUM, (height // QUANTUM + 1) * QUANTUM)

    key = (width, height)
    if key not in _cache:
        # Bounded rather than unbounded: a window dragged across every size
        # would otherwise hold every one of them in memory for the session.
        if len(_cache) > 6:
            _cache.clear()
        _cache[key] = _render(width, height)
    return _cache[key]
