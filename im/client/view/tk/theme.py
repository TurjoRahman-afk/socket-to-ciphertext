"""Palette, type scale, and the shapes Tk does not draw by itself.

Colours are taken from the team's design mockup. Tkinter has no rounded
corners, no shadows and no circular images, so anything with a curve in it is
drawn on a Canvas here rather than faked with a widget.
"""

from __future__ import annotations

import tkinter as tk

# ------------------------------------------------------------------ colour ---
# Straight from the mockup's palette swatches.
ORANGE = "#FF8347"
ORANGE_DEEP = "#FF8A00"
PEACH = "#FFE5B4"
CREAM = "#FFF7E6"
BROWN = "#5C3D1E"

# Derived, for the parts a five-colour palette does not name.
WHITE = "#FFFFFF"
PAGE = "#FFFCF5"
RAIL = "#FFFFFF"
LIST_BG = "#FFFDF8"
HOVER = "#FFF3DF"
SELECTED = "#FFE8C8"
MUTED = "#A08B6E"
HAIRLINE = "#F2E4CE"
ONLINE = "#4CAF50"
AWAY = "#FFB300"
OFFLINE = "#C9BBA5"

#: Avatars with no picture get a colour picked from the name, so the same
#: person is always the same colour without storing anything.
AVATAR_COLOURS = ("#FF8347", "#F7A072", "#E8A87C", "#C38D9E", "#7FB685", "#6C9BCF", "#B79ced")

# -------------------------------------------------------------------- type ---
FONT = "Segoe UI"
H1 = (FONT, 20, "bold")
H2 = (FONT, 13, "bold")
BODY = (FONT, 10)
BODY_BOLD = (FONT, 10, "bold")
SMALL = (FONT, 9)
TINY = (FONT, 8)
BADGE = (FONT, 8, "bold")


def avatar_colour(name: str) -> str:
    """A stable colour for a name. Same person, same colour, every session."""
    return AVATAR_COLOURS[sum(name.encode()) % len(AVATAR_COLOURS)]


def initials(name: str) -> str:
    cleaned = name.lstrip("#").strip()
    if not cleaned:
        return "?"
    parts = cleaned.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return cleaned[:2].upper()


# ------------------------------------------------------------------ shapes ---


def rounded_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float = 12,
    **kwargs,
) -> int:
    """A rounded rectangle, as a smoothed polygon.

    Tk draws no rounded corners. A polygon whose corner points are doubled up
    and then smoothed is the usual way round it, and it renders far better
    than four arcs and three rectangles stitched together.
    """
    radius = min(radius, abs(x2 - x1) / 2, abs(y2 - y1) / 2)
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def circle(canvas: tk.Canvas, x: float, y: float, r: float, **kwargs) -> int:
    """A circle centred on (x, y)."""
    return canvas.create_oval(x - r, y - r, x + r, y + r, **kwargs)


def draw_avatar(
    canvas: tk.Canvas,
    x: float,
    y: float,
    name: str,
    radius: float = 18,
    status: str | None = None,
) -> None:
    """A coloured disc with initials, and an optional presence dot.

    Stands in for the photographs in the mockup. A real one would need an
    image per user, which the protocol does not carry.
    """
    canvas.create_oval(
        x - radius, y - radius, x + radius, y + radius,
        fill=avatar_colour(name), outline="",
    )
    canvas.create_text(
        x, y, text=initials(name), fill=WHITE, font=(FONT, int(radius * 0.72), "bold")
    )
    if status is not None:
        dot = radius * 0.34
        canvas.create_oval(
            x + radius - dot * 1.4, y + radius - dot * 1.4,
            x + radius + dot * 0.6, y + radius + dot * 0.6,
            fill=status, outline=WHITE, width=2,
        )
