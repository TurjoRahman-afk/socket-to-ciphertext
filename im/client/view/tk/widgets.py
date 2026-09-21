"""The pieces the mockup needs that Tk has no widget for.

Each one is a Canvas that draws itself and redraws on resize. They are dumb
on purpose: they take values and paint them, and raise callbacks upward. None
of them reads the model.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from im.client.view.tk import theme as t
from im.client.view.tk.backdrop import backdrop


class PillButton(tk.Canvas):
    """A rounded, filled button. Tk's own button cannot have round corners."""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        fill: str = t.ORANGE,
        text_colour: str = t.WHITE,
        height: int = t.px(38),
        radius: int = t.px(19),
        font: tuple = t.BODY_BOLD,
        bg: str = t.RAIL,
    ) -> None:
        super().__init__(master, height=height, bg=bg, highlightthickness=0)
        self._text = text
        self._command = command
        self._fill = fill
        self._text_colour = text_colour
        self._radius = radius
        self._font = font
        self._enabled = True
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._click)
        self.configure(cursor="hand2")

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2:
            return
        fill = self._fill if self._enabled else t.OFFLINE
        t.rounded_rect(self, 1, 1, w - 1, h - 1, self._radius, fill=fill, outline="")
        self.create_text(w / 2, h / 2, text=self._text, fill=self._text_colour, font=self._font)

    def _click(self, _event: object) -> None:
        if self._enabled:
            self._command()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()


class NavItem(tk.Canvas):
    """One row of the navigation rail: an icon, a label, a selected state."""

    HEIGHT = t.px(42)

    def __init__(
        self,
        master: tk.Misc,
        icon: str,
        label: str,
        command: Callable[[], None],
        *,
        bg: str = t.RAIL,
    ) -> None:
        super().__init__(master, height=self.HEIGHT, bg=bg, highlightthickness=0)
        self.icon = icon
        self.label = label
        self._command = command
        self._bg = bg
        self._selected = False
        self._badge = 0
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", lambda _e: self._command())
        self.bind("<Enter>", lambda _e: self._draw(hover=True))
        self.bind("<Leave>", lambda _e: self._draw())
        self.configure(cursor="hand2")

    def _draw(self, hover: bool = False) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2:
            return
        if self._selected:
            t.rounded_rect(self, 4, 3, w - 4, h - 3, 10, fill=t.SELECTED, outline="")
        elif hover:
            t.rounded_rect(self, 4, 3, w - 4, h - 3, 10, fill=t.HOVER, outline="")

        colour = t.ORANGE_DEEP if self._selected else t.BROWN
        self.create_text(24, h / 2, text=self.icon, fill=colour, font=(t.FONT, 13))
        self.create_text(
            46, h / 2, text=self.label, anchor="w", fill=colour,
            font=t.BODY_BOLD if self._selected else t.BODY,
        )
        if self._badge:
            t.circle(self, w - 24, h / 2, 9, fill=t.ORANGE, outline="")
            self.create_text(w - 24, h / 2, text=str(self._badge), fill=t.WHITE, font=t.BADGE)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._draw()

    def set_badge(self, count: int) -> None:
        self._badge = count
        self._draw()


class ConversationRow(tk.Canvas):
    """One entry in the conversation list: avatar, name, preview, time, unread."""

    HEIGHT = t.px(66)

    def __init__(self, master: tk.Misc, key: str, command: Callable[[str], None]) -> None:
        super().__init__(master, height=self.HEIGHT, bg=t.LIST_BG, highlightthickness=0)
        self.key = key
        self._command = command
        self._preview = ""
        self._time = ""
        self._unread = 0
        self._status: str | None = None
        self._selected = False
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", lambda _e: self._command(self.key))
        self.bind("<Enter>", lambda _e: self._draw(hover=True))
        self.bind("<Leave>", lambda _e: self._draw())
        self.configure(cursor="hand2")

    def update_row(
        self, preview: str, time_text: str, unread: int, status: str | None
    ) -> None:
        self._preview, self._time, self._unread, self._status = (
            preview, time_text, unread, status,
        )
        self._draw()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._draw()

    def _draw(self, hover: bool = False) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2:
            return

        pad = t.px(5)
        if self._selected:
            t.rounded_rect(self, pad, t.px(3), w - pad, h - t.px(3), t.px(12),
                           fill=t.SELECTED, outline="")
            # A bar down the left edge as well as the fill. On a warm palette
            # two shades of the same cream are easy to miss; an edge is not.
            t.rounded_rect(self, pad, t.px(12), pad + t.px(4), h - t.px(12), t.px(2),
                           fill=t.ORANGE_DEEP, outline="")
        elif hover:
            t.rounded_rect(self, pad, t.px(3), w - pad, h - t.px(3), t.px(12),
                           fill=t.HOVER, outline="")

        t.draw_avatar(self, t.px(32), h / 2, self.key, radius=t.px(19), status=self._status)

        left = t.px(60)
        self.create_text(left, t.px(24), text=self.key, anchor="w",
                         fill=t.BROWN, font=t.BODY_BOLD)

        preview = self._preview
        room = max(6, int((w - t.px(130)) / t.px(6.4)))
        if len(preview) > room:
            preview = preview[: room - 1] + "…"
        self.create_text(left, t.px(44), text=preview, anchor="w", fill=t.MUTED, font=t.SMALL)

        if self._time:
            self.create_text(w - t.px(16), t.px(22), text=self._time, anchor="e",
                             fill=t.MUTED, font=t.TINY)
        if self._unread:
            # A pill, not a circle: "12" in a circle sized for "1" overflows
            # it, and 99+ overflows it badly.
            label = "99+" if self._unread > 99 else str(self._unread)
            half = t.px(9) + t.px(4) * (len(label) - 1)
            cx, cy = w - t.px(26), t.px(45)
            t.rounded_rect(self, cx - half, cy - t.px(9), cx + half, cy + t.px(9), t.px(9),
                           fill=t.ORANGE, outline="")
            self.create_text(cx, cy, text=label, fill=t.WHITE, font=t.BADGE)


class Transcript(tk.Canvas):
    """The message pane: rounded bubbles, laid out top to bottom.

    Redraws the whole conversation on any change. That is wasteful for a very
    long history and entirely fine for a chat window, and it removes a whole
    class of bug where the drawn state and the model drift apart.

    The soft background is a generated bitmap rather than drawn shapes --
    backdrop.py explains why it has to be.
    """

    PAD_X = t.px(18)
    GAP = t.px(10)
    AVATAR = t.px(13)
    TAIL = t.px(7)

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=t.CREAM, highlightthickness=0)
        self._messages: list = []
        self._empty_text = ""
        self._notices: list[str] = []
        # Held deliberately: Tk keeps only a weak reference to a PhotoImage,
        # so a background nothing else refers to is collected and the canvas
        # draws a blank rectangle where it used to be.
        self._backdrop: tk.PhotoImage | None = None
        self._resize_after: str | None = None
        self.bind("<Configure>", self._on_resize)
        self.bind("<MouseWheel>", self._scroll)

    def _on_resize(self, _event: object = None) -> None:
        """Redraw now; regenerate the background once the dragging stops.

        Rendering the backdrop takes about a third of a second, which is
        unnoticeable once and unusable on every pixel of a window drag.
        """
        self.redraw()
        if self._resize_after is not None:
            self.after_cancel(self._resize_after)
        self._resize_after = self.after(150, self._refresh_backdrop)

    def _refresh_backdrop(self) -> None:
        self._resize_after = None
        self._backdrop = backdrop(self.winfo_width(), self.winfo_height())
        self.redraw()

    def _scroll(self, event: tk.Event) -> None:
        self.yview_scroll(int(-event.delta / 60), "units")

    def show(self, messages: list, empty_text: str = "") -> None:
        self._messages = list(messages)
        self._empty_text = empty_text
        self._notices.clear()
        self.redraw()

    def notice(self, text: str) -> None:
        """A centred system line -- presence, errors, room changes."""
        self._notices.append(text)
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        if width < 40:
            return

        self._draw_backdrop(width)

        if not self._messages and not self._notices:
            self._draw_empty(width)
            return

        y = t.px(16)
        max_bubble = max(t.px(160), int(width * 0.62))
        last_day = ""

        for message in self._messages:
            day = self._day(message.ts)
            if day and day != last_day:
                y = self._draw_day(day, y, width)
                last_day = day
            y = self._draw_bubble(message, y, width, max_bubble)

        for text in self._notices:
            self.create_text(
                width / 2, y + t.px(10), text=text, fill=t.MUTED, font=t.TINY, anchor="n"
            )
            y += t.px(28)

        self.configure(scrollregion=(0, 0, width, y + t.px(16)))
        self.yview_moveto(1.0)

    def _draw_backdrop(self, width: int) -> None:
        """The generated wash, placed once at the top.

        Not tiled. The first attempt repeated the image down the scroll
        region, and the seam was obvious: the gradient ends dark and restarts
        light, so every tile boundary was a visible horizontal band.

        Placed once instead, and the canvas background is the colour the
        gradient ends on -- so a conversation long enough to scroll past the
        image runs into the same colour it was already approaching, and there
        is no boundary to see.
        """
        if self._backdrop is None:
            self._backdrop = backdrop(width, self.winfo_height())
        if self._backdrop is not None:
            self.create_image(0, 0, image=self._backdrop, anchor="nw")

    def _draw_day(self, day: str, y: float, width: int) -> float:
        """A dated divider, so a long history is not one undifferentiated wall."""
        centre = y + t.px(12)
        label = self.create_text(
            width / 2, centre, text=day, fill=t.MUTED, font=t.TINY, anchor="c"
        )
        x1, _, x2, _ = self.bbox(label)
        rule = t.px(40)
        pad = t.px(14)
        for a, b in ((x1 - pad - rule, x1 - pad), (x2 + pad, x2 + pad + rule)):
            self.create_line(a, centre, b, centre, fill=t.HAIRLINE)
        return y + t.px(34)

    def _draw_bubble(self, message, y: float, width: int, max_bubble: int) -> float:
        mine = message.mine
        fill = t.ORANGE if mine else t.WHITE
        ink = t.WHITE if mine else t.BROWN

        stamp = self._clock(message.ts)
        tick = ""
        if mine:
            # One tick sent, two delivered, two in colour read -- the
            # convention every messenger uses, so it needs no explaining.
            tick = {"SENT": "✓", "DELIVERED": "✓✓", "READ": "✓✓"}.get(
                message.state, ""
            )
        meta = f"{stamp}  {tick}".strip()

        # Measure by drawing the text off-screen first, then wrap the bubble
        # around whatever height it actually took.
        probe = self.create_text(
            -9999, -9999, text=message.body, font=t.BODY,
            width=max_bubble - t.px(28), anchor="nw",
        )
        _, _, px2, py2 = self.bbox(probe)
        self.delete(probe)
        text_w, text_h = px2 + 9999, py2 + 9999

        meta_w = 0
        if meta:
            gauge = self.create_text(-9999, -9999, text=meta, font=t.TINY, anchor="nw")
            _, _, gx2, _ = self.bbox(gauge)
            self.delete(gauge)
            meta_w = gx2 + 9999 + t.px(10)

        # The time sits inside the bubble now, so the bubble has to be wide
        # enough for the text or for the time, whichever needs more room.
        bubble_w = max(text_w + t.px(28), meta_w + t.px(24))
        bubble_h = text_h + t.px(20) + (t.px(12) if meta else 0)

        if mine:
            right = width - self.PAD_X
            left = right - bubble_w
        else:
            left = self.PAD_X + t.px(34)
            right = left + bubble_w
            # Level with the bottom of the bubble, beside the tail, which is
            # where every messenger puts it.
            t.draw_avatar(
                self, self.PAD_X + t.px(14), y + bubble_h - self.AVATAR,
                message.sender, radius=self.AVATAR,
            )

        t.rounded_rect(self, left, y, right, y + bubble_h, t.px(14), fill=fill, outline="")
        self._draw_tail(left, right, y + bubble_h, fill, mine)

        self.create_text(
            left + t.px(14), y + t.px(10), text=message.body, anchor="nw", fill=ink,
            font=t.BODY, width=max_bubble - t.px(28),
        )
        if meta:
            self.create_text(
                right - t.px(12), y + bubble_h - t.px(7), text=meta, anchor="se",
                fill=t.WHITE if mine else t.MUTED, font=t.TINY,
            )
        return y + bubble_h + self.GAP + t.px(6)

    def _draw_tail(self, left: float, right: float, bottom: float, fill: str, mine: bool) -> None:
        """The point at the bottom corner, aimed at whoever spoke.

        A plain triangle: it sits flush against the bubble's own corner
        radius, so the two read as one shape rather than two.
        """
        tail = self.TAIL
        if mine:
            points = (right - tail * 2, bottom - tail, right + tail, bottom, right - tail, bottom)
        else:
            points = (left + tail * 2, bottom - tail, left - tail, bottom, left + tail, bottom)
        self.create_polygon(points, fill=fill, outline="")

    def _draw_empty(self, width: int) -> None:
        height = max(self.winfo_height(), t.px(200))
        cx, cy = width / 2, height / 2 - t.px(30)

        # A small scene rather than one glyph in a circle: offset discs for
        # depth, a speech bubble, and a paper plane leaving it.
        t.circle(self, cx + t.px(16), cy + t.px(10), t.px(52), fill=t.CREAM, outline="")
        t.circle(self, cx, cy, t.px(46), fill=t.PEACH, outline="")
        t.rounded_rect(
            self, cx - t.px(26), cy - t.px(18), cx + t.px(20), cy + t.px(10),
            t.px(10), fill=t.WHITE, outline="",
        )
        self.create_polygon(
            cx - t.px(18), cy + t.px(8), cx - t.px(18), cy + t.px(21), cx - t.px(6), cy + t.px(9),
            fill=t.WHITE, outline="",
        )
        for dx in (-t.px(12), 0, t.px(12)):
            t.circle(self, cx + dx, cy - t.px(4), t.px(3), fill=t.ORANGE, outline="")
        self.create_polygon(
            cx + t.px(26), cy - t.px(28), cx + t.px(56), cy - t.px(14),
            cx + t.px(31), cy - t.px(7), cx + t.px(33), cy - t.px(18),
            fill=t.ORANGE_DEEP, outline="",
        )

        self.create_text(
            cx, cy + t.px(78), text=self._empty_text or "No messages yet",
            fill=t.BROWN, font=t.H2,
        )
        self.create_text(
            cx, cy + t.px(104),
            text="Say something -- it is encrypted before it leaves this machine.",
            fill=t.MUTED, font=t.SMALL,
        )
        self.configure(scrollregion=(0, 0, width, height))

    @staticmethod
    def _day(ts: int) -> str:
        """Today, Yesterday, or the date. Blank for a message with no time."""
        if not ts:
            return ""
        import datetime

        when = datetime.datetime.fromtimestamp(ts / 1000).date()
        today = datetime.date.today()
        if when == today:
            return "Today"
        if (today - when).days == 1:
            return "Yesterday"
        return when.strftime("%d %B %Y")


    @staticmethod
    def _clock(ts: int) -> str:
        if not ts:
            return ""
        import datetime

        return datetime.datetime.fromtimestamp(ts / 1000).strftime("%H:%M")

    def as_text(self) -> str:
        """Everything currently drawn, for tests and for a copy of the log."""
        return "\n".join(
            str(self.itemcget(item, "text"))
            for item in self.find_all()
            if self.type(item) == "text"
        )
