"""The pieces the mockup needs that Tk has no widget for.

Each one is a Canvas that draws itself and redraws on resize. They are dumb
on purpose: they take values and paint them, and raise callbacks upward. None
of them reads the model.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from im.client.view.tk import theme as t


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

        if self._selected:
            t.rounded_rect(self, 5, 3, w - 5, h - 3, 12, fill=t.SELECTED, outline="")
        elif hover:
            t.rounded_rect(self, 5, 3, w - 5, h - 3, 12, fill=t.HOVER, outline="")

        t.draw_avatar(self, 32, h / 2, self.key, radius=19, status=self._status)

        name = self.key
        self.create_text(60, 24, text=name, anchor="w", fill=t.BROWN, font=t.BODY_BOLD)

        preview = self._preview
        room = max(6, int((w - 130) / 6.4))
        if len(preview) > room:
            preview = preview[: room - 1] + "…"
        self.create_text(60, 44, text=preview, anchor="w", fill=t.MUTED, font=t.SMALL)

        if self._time:
            self.create_text(w - 16, 22, text=self._time, anchor="e", fill=t.MUTED, font=t.TINY)
        if self._unread:
            t.circle(self, w - 26, 45, 9, fill=t.ORANGE, outline="")
            self.create_text(
                w - 26, 45, text=str(min(self._unread, 99)), fill=t.WHITE, font=t.BADGE
            )


class Transcript(tk.Canvas):
    """The message pane: rounded bubbles, laid out top to bottom.

    Redraws the whole conversation on any change. That is wasteful for a very
    long history and entirely fine for a chat window, and it removes a whole
    class of bug where the drawn state and the model drift apart.
    """

    PAD_X = t.px(18)
    GAP = t.px(10)

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=t.CREAM, highlightthickness=0)
        self._messages: list = []
        self._empty_text = ""
        self._notices: list[str] = []
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<MouseWheel>", self._scroll)

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

        if not self._messages and not self._notices:
            self._draw_empty(width)
            return

        y = 16
        max_bubble = max(160, int(width * 0.62))

        for message in self._messages:
            y = self._draw_bubble(message, y, width, max_bubble)
        for text in self._notices:
            self.create_text(
                width / 2, y + 10, text=text, fill=t.MUTED, font=t.TINY, anchor="n"
            )
            y += 28

        self.configure(scrollregion=(0, 0, width, y + 16))
        self.yview_moveto(1.0)

    def _draw_bubble(self, message, y: float, width: int, max_bubble: int) -> float:
        mine = message.mine
        fill = t.ORANGE if mine else t.WHITE
        ink = t.WHITE if mine else t.BROWN

        # Measure by drawing the text off-screen first, then wrap the bubble
        # around whatever height it actually took.
        probe = self.create_text(
            -9999, -9999, text=message.body, font=t.BODY, width=max_bubble - 28, anchor="nw"
        )
        x1, y1, x2, y2 = self.bbox(probe)
        self.delete(probe)
        text_w, text_h = x2 - x1, y2 - y1

        bubble_w = text_w + 28
        bubble_h = text_h + 20

        if mine:
            right = width - self.PAD_X
            left = right - bubble_w
        else:
            left = self.PAD_X + 34
            right = left + bubble_w
            t.draw_avatar(self, self.PAD_X + 14, y + 16, message.sender, radius=13)

        t.rounded_rect(self, left, y, right, y + bubble_h, 14, fill=fill, outline="")
        self.create_text(
            left + 14, y + 10, text=message.body, anchor="nw", fill=ink,
            font=t.BODY, width=max_bubble - 28,
        )

        stamp = self._clock(message.ts)
        if mine:
            # One tick sent, two delivered, two in colour read -- the
            # convention every messenger uses, so it needs no explaining.
            tick = {"SENT": "✓", "DELIVERED": "✓✓", "READ": "✓✓"}.get(message.state, "")
            colour = t.ORANGE_DEEP if message.state == "READ" else t.MUTED
            if stamp or tick:
                self.create_text(
                    right, y + bubble_h + 3, text=f"{stamp}  {tick}".strip(),
                    anchor="ne", fill=colour, font=t.TINY,
                )
        elif stamp:
            self.create_text(
                left, y + bubble_h + 3, text=stamp, anchor="nw",
                fill=t.MUTED, font=t.TINY,
            )
        return y + bubble_h + self.GAP + 10

    def _draw_empty(self, width: int) -> None:
        height = max(self.winfo_height(), 200)
        cx, cy = width / 2, height / 2 - 30
        t.circle(self, cx, cy, 44, fill=t.PEACH, outline="")
        self.create_text(cx, cy, text="✉", fill=t.ORANGE_DEEP, font=(t.FONT, 34))
        self.create_text(
            cx, cy + 74, text=self._empty_text or "No messages yet",
            fill=t.BROWN, font=t.H2,
        )
        self.create_text(
            cx, cy + 100, text="Start a new conversation or say hi to your friends!",
            fill=t.MUTED, font=t.SMALL,
        )
        self.configure(scrollregion=(0, 0, width, height))

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
