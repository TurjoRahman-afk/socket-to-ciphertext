"""The Tkinter interface. Phase 7, restyled to the team's design mockup.

A second view over the same ChatModel the console view uses. Nothing below
this package changed to make either of them possible.

The one rule that matters
-------------------------
Tkinter has no sanctioned equivalent of Swing's invokeLater, and calling a
widget method from a worker thread usually corrupts Tk's internal state
*silently* rather than raising -- a freeze or a crash appears minutes later,
somewhere unrelated. So:

    reader thread ---> post_frame() ---> inbox ---+
                                                  |
    root.after(50, _poll) on the main thread <----+  drains it, and only
                                                     this thread touches
                                                     widgets or the model

post_frame and post_state are the only methods a worker thread may call, and
both do nothing but put an item on a queue.

Layout
------
Three columns, following the mockup: a navigation rail, the conversation
list, and the message pane. Everything with a curve in it -- buttons,
bubbles, avatars, unread badges -- is drawn on a Canvas, because Tk has no
rounded corners of its own. Those pieces live in widgets.py; this file wires
them to the model.
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk
from tkinter import messagebox, simpledialog

from im.client.controller.chat import ChatController
from im.client.model.conversation import Message
from im.client.model.events import (
    ConnectionStateChanged,
    ContactsChanged,
    ConversationSelected,
    ErrorRaised,
    Event,
    HistoryLoaded,
    MessageAdded,
    PresenceChanged,
    ReceiptChanged,
    RoomMembersChanged,
    RosterReplaced,
    TypingChanged,
    UnreadChanged,
)
from im.client.view.tk import theme as t
from im.client.view.tk.room_dialog import ask_room
from im.client.view.tk.widgets import ConversationRow, NavItem, PillButton, Transcript
from im.common.frames import Frame

#: How often the main thread drains the queue. Fast enough that a message
#: feels immediate, slow enough that an idle client is not busy-waiting.
log = logging.getLogger(__name__)

POLL_MS = 50

#: How long after the last keystroke we tell the other end we stopped typing.
TYPING_IDLE_MS = 1500

RAIL_WIDTH = t.px(190)
LIST_WIDTH = t.px(280)


class TkView:
    """The window. Renders the model and turns clicks into gestures."""

    def __init__(self, controller: ChatController, root: tk.Misc | None = None) -> None:
        self.controller = controller
        self.model = controller.model

        self._inbox: queue.Queue = queue.Queue()
        self._typing_after: str | None = None
        self._announced_typing = False
        self._rows: dict[str, ConversationRow] = {}

        # A caller may supply the window. The tests do, because creating and
        # destroying a Tk root repeatedly in one process eventually corrupts
        # the Tcl interpreter.
        self.root = root if root is not None else tk.Tk()
        self.root.title("Semaphore")
        self.root.geometry(f"{t.px(1060)}x{t.px(680)}")
        self.root.minsize(t.px(840), t.px(520))
        self.root.configure(bg=t.PAGE)

        # Tk sends callback exceptions to stderr. The app is launched with
        # pythonw so that no console sits behind the window, and pythonw has
        # no stderr -- so a broken button did not look broken, it looked like
        # nothing had happened at all. That cost an evening once already.
        self.root.report_callback_exception = self._on_callback_error

        self._build()
        self._unsubscribe = self.model.subscribe(self._render)
        self.root.protocol("WM_DELETE_WINDOW", self.stop)

    # ----------------------------------------------------- worker-thread API ---

    def post_frame(self, frame: Frame) -> None:
        """Called on the connection's reader thread. Touches no widget."""
        self._inbox.put(("frame", frame))

    def post_state(self, state: str) -> None:
        """Called on whichever thread changed the connection state."""
        self._inbox.put(("state", str(state)))

    # ---------------------------------------------------------------- layout ---

    def _build(self) -> None:
        self.root.columnconfigure(2, weight=1)
        self.root.rowconfigure(0, weight=1)
        self._build_rail()
        self._build_list()
        self._build_chat()

    def _build_rail(self) -> None:
        rail = tk.Frame(self.root, bg=t.RAIL, width=RAIL_WIDTH)
        rail.grid(row=0, column=0, sticky="nsew")
        # pack_propagate, not grid_propagate: everything in the rail is
        # packed, and grid_propagate governs grid children only. With the
        # wrong one the rail widened to fit its widest child -- an
        # unconfigured Canvas, which Tk makes 378 pixels -- so the rail came
        # out at 598 instead of the 285 it asks for. The conversation list
        # next door uses grid children, which is why its width was always
        # exactly right and this one never was.
        rail.pack_propagate(False)
        rail.columnconfigure(0, weight=1)

        logo = tk.Canvas(rail, height=t.px(64), bg=t.RAIL, highlightthickness=0)
        logo.pack(fill="x", padx=16, pady=(16, 8))
        logo.bind("<Configure>", lambda _e: self._draw_logo(logo))

        PillButton(rail, "+  New Message", self._new_conversation).pack(
            fill="x", padx=16, pady=(4, 14)
        )

        self.nav: dict[str, NavItem] = {}
        for key, icon, label, command in (
            ("chats", "💬", "Chats", lambda: None),
            ("contacts", "👥", "Contacts", self._show_contacts),
            ("rooms", "#", "Rooms", self._room_menu),
            ("search", "🔍", "Search", self._search),
            ("settings", "⚙", "Settings", self._show_settings),
        ):
            item = NavItem(rail, icon, label, command)
            item.pack(fill="x", padx=10, pady=1)
            self.nav[key] = item
        self.nav["chats"].set_selected(True)

        self.me = tk.Canvas(rail, height=t.px(58), bg=t.RAIL, highlightthickness=0)
        self.me.pack(side="bottom", fill="x", padx=8, pady=10)
        self.me.bind("<Configure>", lambda _e: self._draw_me())

    def _draw_logo(self, canvas: tk.Canvas) -> None:
        canvas.delete("all")
        h = canvas.winfo_height()
        t.rounded_rect(canvas, 4, h / 2 - 17, 38, h / 2 + 17, 11, fill=t.ORANGE, outline="")
        canvas.create_text(21, h / 2 - 1, text="💬", font=(t.FONT, 13), fill=t.WHITE)
        canvas.create_text(
            50, h / 2, text="Semaphore", anchor="w", fill=t.ORANGE_DEEP, font=t.H1
        )

    def _draw_me(self) -> None:
        self.me.delete("all")
        w, h = self.me.winfo_width(), self.me.winfo_height()
        if w < 2:
            return
        name = self.model.username or "…"
        t.draw_avatar(self.me, 26, h / 2, name, radius=17, status=t.ONLINE)
        self.me.create_text(50, h / 2 - 8, text=name, anchor="w", fill=t.BROWN, font=t.BODY_BOLD)
        self.me.create_text(
            50, h / 2 + 9, text=self.model.connection_state.title(), anchor="w",
            fill=t.MUTED, font=t.TINY,
        )

    def _build_list(self) -> None:
        panel = tk.Frame(self.root, bg=t.LIST_BG, width=LIST_WIDTH)
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_propagate(False)
        panel.rowconfigure(1, weight=1)
        panel.columnconfigure(0, weight=1)

        head = tk.Frame(panel, bg=t.LIST_BG, height=t.px(54))
        head.grid(row=0, column=0, sticky="ew")
        head.grid_propagate(False)
        tk.Label(
            head, text="Chats", bg=t.LIST_BG, fg=t.BROWN, font=t.H2, anchor="w"
        ).pack(side="left", padx=18, pady=14)
        self.unread_label = tk.Label(head, text="", bg=t.LIST_BG, fg=t.MUTED, font=t.TINY)
        self.unread_label.pack(side="right", padx=16)

        self.list_frame = tk.Frame(panel, bg=t.LIST_BG)
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=2)
        self.list_frame.columnconfigure(0, weight=1)

        self.empty_list = tk.Label(
            self.list_frame,
            text="No conversations yet.\nPress New Message to start one.",
            bg=t.LIST_BG, fg=t.MUTED, font=t.SMALL, justify="center",
        )
        self.empty_list.grid(row=0, column=0, pady=40)

    def _build_chat(self) -> None:
        pane = tk.Frame(self.root, bg=t.CREAM)
        pane.grid(row=0, column=2, sticky="nsew")
        pane.rowconfigure(2, weight=1)
        pane.columnconfigure(0, weight=1)

        self.header = tk.Canvas(pane, height=t.px(64), bg=t.WHITE, highlightthickness=0)
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.bind("<Configure>", lambda _e: self._draw_header())

        # A coloured strip across the whole pane when the connection is not
        # healthy. RETRYING used to be five grey words at the bottom of the
        # rail, which is easy to miss entirely -- and not knowing you are
        # disconnected is the worst state a messenger can put you in. It
        # occupies no height at all when everything is fine.
        self.status_strip = tk.Canvas(pane, height=0, bg=t.ORANGE_DEEP, highlightthickness=0)
        self.status_strip.grid(row=1, column=0, sticky="ew")
        self.status_strip.bind("<Configure>", lambda _e: self._draw_strip())
        self.status_strip.grid_remove()
        self._strip_text = ""

        self.transcript = Transcript(pane)
        self.transcript.grid(row=2, column=0, sticky="nsew")

        composer = tk.Frame(pane, bg=t.CREAM)
        composer.grid(row=3, column=0, sticky="ew", padx=t.px(16), pady=t.px(14))
        composer.columnconfigure(0, weight=1)

        box = tk.Frame(composer, bg=t.WHITE, highlightthickness=1, highlightbackground=t.HAIRLINE)
        box.grid(row=0, column=0, sticky="ew", ipady=t.px(7))
        self.entry = tk.Entry(
            box, font=t.BODY, relief="flat", bg=t.WHITE, fg=t.BROWN,
            insertbackground=t.ORANGE_DEEP,
        )
        self.entry.pack(fill="x", padx=t.px(14))
        self.entry.bind("<Return>", self._on_send)
        self.entry.bind("<Key>", self._on_key)

        self.send_button = PillButton(
            composer, "➤", self._on_send, height=t.px(42), radius=t.px(21),
            font=t.font(13), bg=t.CREAM,
        )
        self.send_button.configure(width=52)
        self.send_button.grid(row=0, column=1, padx=(10, 0))
        self._set_composer(enabled=False)

    def _draw_header(self) -> None:
        self.header.delete("all")
        w, h = self.header.winfo_width(), self.header.winfo_height()
        if w < 2:
            return
        self.header.create_line(0, h - 1, w, h - 1, fill=t.HAIRLINE)

        active = self.model.active
        if active is None:
            self.header.create_text(
                t.px(24), h / 2, text="Select a conversation", anchor="w",
                fill=t.MUTED, font=t.BODY,
            )
            return

        room = active.startswith("#")
        t.draw_avatar(
            self.header, t.px(36), h / 2, active, radius=t.px(19),
            status=None if room else (t.ONLINE if self.model.is_online(active) else t.OFFLINE),
        )
        self.header.create_text(
            t.px(66), h / 2 - t.px(9), text=active, anchor="w", fill=t.BROWN, font=t.H2
        )

        typing = self.model.typing_in(active)
        if typing:
            subtitle, colour = f"{', '.join(typing)} is typing…", t.ORANGE_DEEP
        elif room:
            members = self.model.room_members(active)
            subtitle, colour = f"{len(members)} members", t.MUTED
        else:
            subtitle = "Online" if self.model.is_online(active) else "Offline"
            colour = t.ONLINE if self.model.is_online(active) else t.MUTED
        self.header.create_text(
            t.px(66), h / 2 + t.px(10), text=subtitle, anchor="w", fill=colour, font=t.TINY
        )

        # Only the one icon, and only on a room. The mockup also showed a call
        # button, a video button and an overflow menu; none of them has
        # anything behind it, and an icon that does nothing when clicked is
        # worse than an icon that is not there.
        if room:
            item = self.header.create_text(
                w - t.px(24), h / 2, text="👤+", fill=t.ORANGE_DEEP, font=t.font(12)
            )
            self.header.tag_bind(item, "<Button-1>", lambda _e: self._invite_menu())

    # -------------------------------------------------------------- the loop ---

    def run(self) -> None:
        self.root.after(POLL_MS, self._poll)
        self._refresh_list()
        self._redraw()
        self.root.mainloop()

    def _poll(self) -> None:
        """Drain the queue on the main thread. The whole bridge, in one method."""
        try:
            while True:
                kind, payload = self._inbox.get_nowait()
                try:
                    if kind == "frame":
                        self.controller.on_frame(payload)
                    elif kind == "state":
                        self.controller.on_state(payload)
                except Exception as exc:  # noqa: BLE001 -- a view must not die
                    self.transcript.notice(f"{type(exc).__name__}: {exc}")
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    def stop(self) -> None:
        self._unsubscribe()
        self.root.quit()
        self.root.destroy()

    # -------------------------------------------------------------- gestures ---

    def _on_send(self, _event: object = None) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._stop_typing()
        if self.controller.send(text) is None:
            self.transcript.notice("not sent — pick a conversation first")

    def _on_key(self, _event: object) -> None:
        """Announce typing, and stop announcing once the keys go quiet."""
        if self.model.active is None:
            return
        if not self._announced_typing:
            self._announced_typing = True
            self.controller.typing(True)
        if self._typing_after is not None:
            self.root.after_cancel(self._typing_after)
        self._typing_after = self.root.after(TYPING_IDLE_MS, self._stop_typing)

    def _stop_typing(self) -> None:
        if self._typing_after is not None:
            self.root.after_cancel(self._typing_after)
            self._typing_after = None
        if self._announced_typing:
            self._announced_typing = False
            self.controller.typing(False)

    def _new_conversation(self) -> None:
        who = simpledialog.askstring("New message", "Who do you want to talk to?", parent=self.root)
        if not who or not who.strip():
            return
        # Opens once the server confirms the account exists, and keeps them so
        # the conversation survives a restart. Opening first and asking after
        # is what put a chat window on screen for a name nobody had
        # registered.
        self.controller.open_conversation(who.strip())

    def _room_menu(self) -> None:
        """Make a room, with whoever should be in it.

        The name decides which of the two things this does. A room we are
        already in is an invitation; anything else is a new room. Asking the
        user to say which they meant would be a question they should not have
        to answer, because the answer is already on screen.
        """
        answer = ask_room(
            self.root,
            self._contacts(),
            online=self.model.is_online,
        )
        if answer is None:
            return

        # The controller decides between making and joining, and corrects
        # itself if it guesses wrong. It opens the room once the server has
        # confirmed we are actually in it.
        self.controller.open_room(self._hashed(answer[0]), answer[1])

    def _invite_menu(self) -> None:
        """Add people to the room on screen."""
        room = self.model.active
        if room is None or not room.startswith("#"):
            messagebox.showinfo(
                "Add people",
                "Open a room first. People are added to rooms, not to direct messages.",
                parent=self.root,
            )
            return

        already = set(self.model.room_members(room))
        answer = ask_room(
            self.root,
            self._contacts(),
            title=f"Add people to {room}",
            room=room,
            online=self.model.is_online,
            already_in=already,
        )
        if answer is not None and answer[1]:
            self.controller.invite(room, answer[1])

    def _contacts(self) -> list[str]:
        """The people we have kept, online first.

        Contacts, not presence. Anybody else can be reached by typing their
        name -- in the room dialog, or in the contacts window.
        """
        return self.model.contact_names()

    @staticmethod
    def _hashed(room: str) -> str:
        room = room.strip()
        return room if room.startswith("#") else f"#{room}"

    def _search(self) -> None:
        """Find a message, and jump to where it was said.

        Client-side, and it can only be client-side: the server holds
        ciphertext it has no key for. What is searchable is what this client
        has loaded, so unloaded scrollback is offered as a history fetch
        rather than silently missed.
        """
        query = simpledialog.askstring("Search", "Find messages containing:", parent=self.root)
        if not query or not query.strip():
            return

        hits = self.model.search(query)
        if not hits:
            messagebox.showinfo(
                "Search",
                f"Nothing loaded here contains {query.strip()!r}.\n\n"
                "Only messages this client has loaded can be searched -- the "
                "server stores ciphertext it cannot read. Open a conversation "
                "and scroll back to load more.",
                parent=self.root,
            )
            return

        self._show_hits(query.strip(), hits)

    def _show_hits(self, query: str, hits: list) -> None:
        """A list of matches; picking one opens that conversation."""
        window = tk.Toplevel(self.root)
        window.title(f"{len(hits)} matches for {query!r}")
        window.configure(bg=t.PAGE)
        window.transient(self.root)

        listbox = tk.Listbox(
            window,
            bg=t.WHITE,
            fg=t.BROWN,
            font=t.BODY,
            relief="flat",
            highlightthickness=0,
            selectbackground=t.SELECTED,
            selectforeground=t.BROWN,
            width=64,
            height=min(len(hits), 14),
            activestyle="none",
        )
        listbox.pack(fill="both", expand=True, padx=t.px(14), pady=t.px(14))

        for hit in hits:
            who = "you" if hit.message.mine else hit.message.sender
            listbox.insert("end", f"{hit.key}  --  {who}:  {hit.snippet(query)}")

        def open_selected(_event: object = None) -> None:
            picked = listbox.curselection()
            if picked:
                self.controller.select(hits[picked[0]].key)
                window.destroy()

        listbox.bind("<Double-Button-1>", open_selected)
        listbox.bind("<Return>", open_selected)
        window.bind("<Escape>", lambda _e: window.destroy())
        self._centre(window)
        listbox.focus_set()

    def _show_contacts(self) -> None:
        """The contact list, with a way to add to it.

        This was a read-only box listing whoever happened to be online, which
        is presence rather than contacts -- so it was empty on startup and
        never held anybody you had added.
        """
        window = tk.Toplevel(self.root)
        window.title("Contacts")
        window.configure(bg=t.PAGE)
        window.transient(self.root)
        window.resizable(False, False)

        frame = tk.Frame(window, bg=t.PAGE)
        frame.pack(fill="both", expand=True, padx=t.px(16), pady=t.px(16))

        listbox = tk.Listbox(
            frame, bg=t.WHITE, fg=t.BROWN, font=t.BODY, relief="flat",
            highlightthickness=1, highlightbackground=t.HAIRLINE,
            selectbackground=t.SELECTED, selectforeground=t.BROWN,
            width=34, height=10, activestyle="none",
        )
        listbox.pack(fill="both", expand=True)

        names = self._contacts()
        if names:
            for name in names:
                dot = "●" if self.model.is_online(name) else "○"
                listbox.insert("end", f" {dot}  {name}")
        else:
            listbox.insert(
                "end", "  Nobody yet. Add somebody below, or message them."
            )

        tk.Label(
            frame, text="Add someone by username", bg=t.PAGE, fg=t.BROWN,
            font=t.BODY_BOLD, anchor="w",
        ).pack(fill="x", pady=(t.px(14), t.px(4)))

        entry = tk.Entry(
            frame, font=t.BODY, bg=t.WHITE, fg=t.BROWN, relief="flat", width=1,
            insertbackground=t.BROWN, highlightthickness=1,
            highlightbackground=t.HAIRLINE, highlightcolor=t.ORANGE,
        )
        entry.pack(fill="x", ipady=t.px(5))

        def add(_event: object = None) -> None:
            name = entry.get().strip()
            if not name:
                return
            # The server answers NO_SUCH_USER when there is no such account,
            # and that error already reaches the transcript. Nothing is
            # invented here about whether it worked.
            self.controller.open_conversation(name)
            window.destroy()

        entry.bind("<Return>", add)
        PillButton(frame, "Add", add, bg=t.PAGE).pack(fill="x", pady=(t.px(10), 0))

        def open_selected(_event: object = None) -> None:
            picked = listbox.curselection()
            if picked and names:
                self.controller.select(names[picked[0]])
                window.destroy()

        listbox.bind("<Double-Button-1>", open_selected)
        window.bind("<Escape>", lambda _e: window.destroy())
        self._centre(window)
        entry.focus_set()


    def _show_settings(self) -> None:
        model = self.model
        messagebox.showinfo(
            "Settings",
            f"Signed in as {model.username}\n"
            f"Connection: {model.connection_state}\n"
            f"Conversations: {len(model.conversations)}\n"
            f"Rooms: {', '.join(model.my_rooms()) or 'none'}\n"
            f"Unread: {model.unread_total()}",
            parent=self.root,
        )

    # ------------------------------------------------------------- rendering ---

    def _render(self, event: Event) -> None:
        """The model's observer. Only ever called from the main thread."""
        if isinstance(event, MessageAdded):
            if event.conversation == self.model.active:
                self._redraw()
            self._refresh_list()
        elif isinstance(event, ReceiptChanged):
            if event.conversation == self.model.active:
                self._redraw()
        elif isinstance(event, ConversationSelected | HistoryLoaded):
            self._redraw()
            self._refresh_list()
        elif isinstance(event, UnreadChanged | RoomMembersChanged | RosterReplaced):
            self._refresh_list()
        elif isinstance(event, PresenceChanged):
            self._refresh_list()
            self._draw_header()
            self.transcript.notice(
                f"{event.user} is {'online' if event.online else 'offline'}"
            )
        elif isinstance(event, TypingChanged):
            self._draw_header()
        elif isinstance(event, ConnectionStateChanged):
            self._set_composer(enabled=event.state == "ONLINE")
            self._show_state(str(event.state))
            self._draw_me()
        elif isinstance(event, ContactsChanged):
            self._refresh_list()
        elif isinstance(event, ErrorRaised):
            self.transcript.notice(f"{event.code}: {event.message}")

    def _centre(self, window: tk.Toplevel) -> None:
        """Put a window over the one it belongs to, not in the screen corner.

        Tk places a new Toplevel wherever the window manager likes, which on
        Windows is the top left. A dialog that opens away from the window it
        came from is easy to miss entirely.
        """
        window.update_idletasks()
        try:
            x = self.root.winfo_rootx() + (self.root.winfo_width() - window.winfo_width()) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - window.winfo_height()) // 3
        except tk.TclError:  # pragma: no cover -- the window went away
            return
        window.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _on_callback_error(self, exc_type, value, trace) -> None:
        """Show what Tk would otherwise have thrown away."""
        log.exception("callback failed", exc_info=(exc_type, value, trace))
        messagebox.showerror(
            "Something went wrong",
            f"{exc_type.__name__}: {value}\n\n"
            "This is a bug in Semaphore, not something you did. "
            "The details are in the log.",
            parent=self.root,
        )

    def _set_composer(self, *, enabled: bool) -> None:
        """The composer is enabled in exactly one connection state."""
        self.entry.config(state="normal" if enabled else "disabled")
        self.send_button.set_enabled(enabled)

    #: What to say for each unhealthy state, and in what colour. ONLINE is
    #: absent on purpose: a healthy connection says nothing at all.
    STRIP = {
        "CONNECTING": ("Connecting\u2026", "#C9A227"),
        "AUTHENTICATING": ("Signing in\u2026", "#C9A227"),
        "RETRYING": ("Reconnecting\u2026 messages you send now will wait", "#C9622D"),
        "CLOSED": ("Disconnected. Nothing will be sent or received.", "#B4452B"),
        "DISCONNECTED": ("Not connected.", "#B4452B"),
    }

    def _show_state(self, state: str) -> None:
        """Raise or hide the strip for a connection state."""
        entry = self.STRIP.get(state.upper())
        if entry is None:
            # Removed from the grid rather than given height 0: Tk will not
            # make a Canvas shorter than one pixel, and one pixel of orange
            # under the header is a line nobody asked for.
            self._strip_text = ""
            self.status_strip.grid_remove()
            return
        self._strip_text, colour = entry
        self.status_strip.configure(height=t.px(26), bg=colour)
        self.status_strip.grid()
        self._draw_strip()

    def _draw_strip(self) -> None:
        self.status_strip.delete("all")
        w = self.status_strip.winfo_width()
        if w < 2 or not self._strip_text:
            return
        self.status_strip.create_text(
            w / 2, self.status_strip.winfo_height() / 2, text=self._strip_text,
            fill=t.WHITE, font=t.SMALL,
        )

    def _refresh_list(self) -> None:
        keys = self.model.keys()

        for key in list(self._rows):
            if key not in keys:
                self._rows.pop(key).destroy()

        self.empty_list.grid_remove() if keys else self.empty_list.grid()

        for index, key in enumerate(keys):
            row = self._rows.get(key)
            if row is None:
                row = ConversationRow(self.list_frame, key, self.controller.select)
                self._rows[key] = row
            row.grid(row=index, column=0, sticky="ew", padx=6, pady=1)

            conversation = self.model.conversation(key)
            last = conversation.last()
            preview = "" if last is None else f"{'You: ' if last.mine else ''}{last.body}"
            row.update_row(
                preview=preview,
                time_text=Transcript._clock(last.ts) if last else "",
                unread=conversation.unread,
                status=None
                if key.startswith("#")
                else (t.ONLINE if self.model.is_online(key) else t.OFFLINE),
            )
            row.set_selected(key == self.model.active)

        total = self.model.unread_total()
        self.unread_label.config(text=f"{total} unread" if total else "")
        self.nav["chats"].set_badge(total)

    def _redraw(self) -> None:
        self._draw_header()
        active = self.model.active
        if active is None:
            self.transcript.show([], "No messages yet")
            return
        self.transcript.show(self.model.conversation(active).messages, f"Say hi to {active}")

    # --------------------------------------------------------------- testing ---

    def rendered_text(self) -> str:
        """Everything currently drawn in the message pane.

        The transcript is a Canvas rather than a Text widget, because Tk
        cannot draw a rounded bubble any other way. This is how a test reads
        what the user can see.
        """
        return self.transcript.as_text()

    def conversation_keys(self) -> list[str]:
        """The conversation list, in the order drawn."""
        return self.model.keys()

    def _append(self, message: Message) -> None:
        """Kept so a caller can push one message without a full redraw."""
        self._redraw()
