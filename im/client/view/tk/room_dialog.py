"""The dialog for starting a room, and for adding people to one.

Split out of app.py because it is the one part of the interface with real
input handling in it, and because both of the things it does -- create with
members, invite into an existing room -- are the same form with a different
title and a different button.

It reads a list of names and returns a list of names. It does not touch the
controller, the model or the protocol: the caller decides what to do with the
answer, which is what makes it testable without a server.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from im.client.view.tk import theme as t
from im.client.view.tk.widgets import PillButton

#: More than this and the list scrolls rather than growing past the screen.
VISIBLE_ROWS = 7

ROW_HEIGHT = t.px(30)

#: Every widget in here is a Canvas or sits next to one, and an unconfigured
#: Tk Canvas is 378 pixels wide -- which is how a dialog asking for one short
#: name ends up wider than the window behind it. Stated once, used everywhere.
WIDTH = t.px(330)


#: The server's rule, checked here as well. Finding out from a red notice in
#: a room that was never created is a bad way to learn your name had a space
#: in it -- the dialog is still open, so it can just say so.
MAX_NAME = 31


def name_problem(name: str) -> str | None:
    """Why this room name will not do, or None if it is fine."""
    if not name:
        return "A room needs a name."
    if any(character.isspace() for character in name):
        return "A room name cannot contain spaces. Try study-group."
    if len(name.lstrip("#")) > MAX_NAME:
        return f"A room name is at most {MAX_NAME} characters."
    if not name.lstrip("#"):
        return "A room needs a name after the #."
    return None


class RoomDialog(tk.Toplevel):
    """Ask for a room name and a set of members.

    Modal, because everything behind it is a view of state this dialog is
    about to change. `result` is None if it was cancelled, otherwise
    (room_name, [members]).
    """

    def __init__(
        self,
        master: tk.Misc,
        contacts: list[str],
        *,
        title: str = "New room",
        room: str | None = None,
        online: Callable[[str], bool] | None = None,
        already_in: set[str] | None = None,
    ) -> None:
        super().__init__(master)
        self.result: tuple[str, list[str]] | None = None
        self._online = online or (lambda _name: False)
        self._already_in = already_in or set()
        # A room name that is fixed means we are inviting into a room that
        # exists, so the name field is shown but not editable -- it is
        # context, not a question.
        self._fixed_room = room
        self._checks: dict[str, tk.BooleanVar] = {}

        self.title(title)
        self.configure(bg=t.PAGE)
        self.resizable(False, False)
        self.transient(master)

        self._build(contacts)

        # Centre on the parent rather than the screen. A dialog that opens
        # over the window it belongs to is much easier to notice.
        self.update_idletasks()
        self._centre_on(master)

        self.grab_set()
        self._name_entry.focus_set()
        self.bind("<Return>", lambda _e: self._accept())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    # ------------------------------------------------------------- building ---

    def _build(self, contacts: list[str]) -> None:
        pad = t.px(18)
        frame = tk.Frame(self, bg=t.PAGE)
        frame.pack(fill="both", expand=True, padx=pad, pady=pad)

        tk.Label(
            frame, text="Room name", bg=t.PAGE, fg=t.BROWN, font=t.BODY_BOLD, anchor="w"
        ).pack(fill="x")

        # Not _name: Tk keeps a widget's own Tcl path name in Misc._name,
        # and shadowing it breaks destroy() in a way that only shows up
        # when the dialog closes.
        self._name_var = tk.StringVar(value=(self._fixed_room or "").lstrip("#"))
        self._name_entry = tk.Entry(
            frame,
            textvariable=self._name_var,
            font=t.BODY,
            bg=t.WHITE,
            fg=t.BROWN,
            relief="flat",
            insertbackground=t.BROWN,
            highlightthickness=1,
            highlightbackground=t.HAIRLINE,
            highlightcolor=t.ORANGE,
        )
        self._name_entry.pack(fill="x", ipady=t.px(6), pady=(t.px(6), 0))
        self._name_entry.configure(width=1)  # width comes from the pack, not from a character count

        # Reserved rather than created on demand, so the dialog does not
        # change height the moment you get something wrong.
        self._complaint = tk.Label(
            frame, text="", bg=t.PAGE, fg=t.ORANGE_DEEP, font=t.TINY,
            anchor="w", wraplength=WIDTH,
        )
        self._complaint.pack(fill="x", pady=(t.px(4), 0))
        if self._fixed_room is not None:
            self._name_entry.configure(state="readonly", readonlybackground=t.CREAM)

        tk.Label(
            frame,
            text="Members",
            bg=t.PAGE,
            fg=t.BROWN,
            font=t.BODY_BOLD,
            anchor="w",
        ).pack(fill="x", pady=(t.px(16), 0))

        selectable = [name for name in contacts if name not in self._already_in]
        if selectable:
            self._member_list(frame, selectable)
        else:
            tk.Label(
                frame,
                text=(
                    "Everyone you know is already in this room."
                    if self._already_in
                    else "Nobody else has been seen online yet.\n"
                    "You can still make the room and add people later."
                ),
                bg=t.PAGE,
                fg=t.MUTED,
                font=t.SMALL,
                justify="left",
                anchor="w",
                wraplength=WIDTH,
            ).pack(fill="x", pady=(t.px(6), 0))

        self._extra_names(frame)
        self._buttons(frame)

    def _extra_names(self, parent: tk.Misc) -> None:
        """A field for names that are not in the list.

        The tick list can only offer people this client knows about, and
        somebody who has never been online in this session is not among them.
        Without this there was no way to put them in a room at all -- which
        read as the dialog silently refusing to add anyone.
        """
        tk.Label(
            parent, text="Or type usernames, separated by commas", bg=t.PAGE,
            fg=t.BROWN, font=t.BODY_BOLD, anchor="w", wraplength=WIDTH,
        ).pack(fill="x", pady=(t.px(14), t.px(4)))

        self._typed = tk.Entry(
            parent, font=t.BODY, bg=t.WHITE, fg=t.BROWN, relief="flat", width=1,
            insertbackground=t.BROWN, highlightthickness=1,
            highlightbackground=t.HAIRLINE, highlightcolor=t.ORANGE,
        )
        self._typed.pack(fill="x", ipady=t.px(5))

        tk.Label(
            parent,
            text="A name with no account is reported, not ignored.",
            bg=t.PAGE, fg=t.MUTED, font=t.TINY, anchor="w", wraplength=WIDTH,
        ).pack(fill="x", pady=(t.px(4), 0))

    def _member_list(self, parent: tk.Misc, contacts: list[str]) -> None:
        """A checkbox per contact, scrolling once there are too many.

        A Canvas with a Frame inside it is the standard way to scroll
        arbitrary widgets in Tk -- there is no scrollable container.
        """
        holder = tk.Frame(parent, bg=t.WHITE, highlightthickness=1, highlightbackground=t.HAIRLINE)
        holder.pack(fill="x", pady=(t.px(6), 0))

        canvas = tk.Canvas(holder, bg=t.WHITE, width=WIDTH, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=t.WHITE)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        if len(contacts) > VISIBLE_ROWS:
            bar = tk.Scrollbar(holder, orient="vertical", command=canvas.yview)
            bar.pack(side="right", fill="y")
            canvas.configure(yscrollcommand=bar.set)
            canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-e.delta // 120, "units"))

        for name in contacts:
            self._row(inner, name)

        # Height from what the rows actually came out as, not from a guess at
        # what a row costs. A Checkbutton's height depends on the font, which
        # depends on the display -- so ROW_HEIGHT is only the scroll cap.
        inner.update_idletasks()
        canvas.configure(height=min(inner.winfo_reqheight(), ROW_HEIGHT * VISIBLE_ROWS))

        # Keep the inner frame as wide as the canvas, or the rows are only as
        # wide as their text and the hover area looks wrong.
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))

    def _row(self, parent: tk.Misc, name: str) -> None:
        var = tk.BooleanVar(value=False)
        self._checks[name] = var

        row = tk.Frame(parent, bg=t.WHITE, height=ROW_HEIGHT)
        row.pack(fill="x")

        check = tk.Checkbutton(
            row,
            text=f"  {name}",
            variable=var,
            bg=t.WHITE,
            fg=t.BROWN,
            activebackground=t.HOVER,
            activeforeground=t.BROWN,
            selectcolor=t.WHITE,
            font=t.BODY,
            anchor="w",
            relief="flat",
            highlightthickness=0,
            cursor="hand2",
        )
        check.pack(side="left", fill="x", expand=True, padx=(t.px(6), 0))

        # A presence dot, so you can see who will actually be there to read it.
        tk.Label(
            row,
            text="●" if self._online(name) else "○",
            bg=t.WHITE,
            fg=t.ONLINE if self._online(name) else t.OFFLINE,
            font=t.TINY,
        ).pack(side="right", padx=(0, t.px(10)))

        # The whole row toggles, not just the little box. Hitting a 13-pixel
        # target is not something anybody should have to do.
        for widget in (row, check):
            widget.bind("<Button-1>", lambda _e: var.set(not var.get()), add="+")

    def _buttons(self, parent: tk.Misc) -> None:
        bar = tk.Frame(parent, bg=t.PAGE)
        bar.pack(fill="x", pady=(t.px(18), 0))

        # Widths are stated rather than left to expand: a PillButton is a
        # Canvas, and an unconfigured Canvas is 378 pixels wide.
        half = WIDTH // 2 - t.px(4)

        cancel = PillButton(
            bar, "Cancel", self._cancel, fill=t.CREAM, text_colour=t.BROWN, bg=t.PAGE
        )
        cancel.configure(width=half)
        cancel.pack(side="right", padx=(t.px(8), 0))

        accept = PillButton(
            bar, "Add" if self._fixed_room is not None else "Create", self._accept, bg=t.PAGE
        )
        accept.configure(width=half)
        accept.pack(side="right")

    def _centre_on(self, master: tk.Misc) -> None:
        try:
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
            y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        except tk.TclError:
            return
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    # --------------------------------------------------------------- answers ---

    @property
    def selected(self) -> list[str]:
        """Who is ticked, plus anybody typed in, without duplicates."""
        picked = [name for name, var in self._checks.items() if var.get()]
        typed = getattr(self, "_typed", None)
        if typed is not None:
            for name in typed.get().replace(";", ",").split(","):
                name = name.strip()
                if name and name not in picked and name not in self._already_in:
                    picked.append(name)
        return picked

    def _accept(self) -> None:
        name = self._name_var.get().strip()
        problem = name_problem(name)
        if problem is not None:
            self._complaint.configure(text=problem)
            self._name_entry.configure(highlightbackground=t.ORANGE_DEEP)
            self._name_entry.focus_set()
            return
        self.result = (name, self.selected)
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


def ask_room(
    master: tk.Misc,
    contacts: list[str],
    **kwargs,
) -> tuple[str, list[str]] | None:
    """Open the dialog and wait for it. None if it was cancelled."""
    dialog = RoomDialog(master, contacts, **kwargs)
    master.wait_window(dialog)
    return dialog.result
