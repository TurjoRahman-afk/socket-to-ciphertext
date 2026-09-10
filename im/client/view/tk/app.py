"""The Tkinter interface. Phase 7.

A second view over the same ChatModel the console view uses. Nothing below
this package changed to make it possible -- that was the point of keeping the
model free of any interface for six phases.

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

Layout follows the mockup in the team's design document: a navigation rail, a
conversation list, and a message pane.
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from im.client.controller.chat import ChatController
from im.client.model.conversation import Message
from im.client.model.events import (
    ConnectionStateChanged,
    ConversationSelected,
    ErrorRaised,
    Event,
    HistoryLoaded,
    MessageAdded,
    PresenceChanged,
    RoomMembersChanged,
    RosterReplaced,
    TypingChanged,
    UnreadChanged,
)
from im.common.frames import Frame

#: How often the main thread drains the queue. Fast enough that a message
#: feels immediate, slow enough that an idle client is not busy-waiting.
POLL_MS = 50

#: How long after the last keystroke we tell the other end we stopped typing.
TYPING_IDLE_MS = 1500

# Taken from the mockup in the design document.
PURPLE = "#6D3BC4"
CREAM = "#FDF6E7"
LIST_BG = "#FBF1DC"
AMBER = "#F3C563"
AMBER_DEEP = "#EFB43C"
BUBBLE = "#FFFFFF"
BUBBLE_MINE = "#FFF8E6"
INK = "#2A2118"
MUTED = "#8A7A62"
ORANGE = "#E2801E"


class TkView:
    """The window. Renders the model and turns clicks into gestures."""

    def __init__(self, controller: ChatController) -> None:
        self.controller = controller
        self.model = controller.model

        self._inbox: queue.Queue = queue.Queue()
        self._typing_after: str | None = None
        self._announced_typing = False

        self.root = tk.Tk()
        self.root.title("Semaphore")
        self.root.geometry("940x600")
        self.root.minsize(720, 420)
        self.root.configure(bg=CREAM)

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
        self.root.rowconfigure(1, weight=1)

        bar = tk.Frame(self.root, bg=PURPLE, height=34)
        bar.grid(row=0, column=0, columnspan=3, sticky="ew")
        bar.grid_propagate(False)
        self.title_label = tk.Label(
            bar, text="  Semaphore", bg=PURPLE, fg="white",
            font=("Segoe UI", 11, "bold"), anchor="w",
        )
        self.title_label.pack(side="left", fill="y")
        self.status_label = tk.Label(
            bar, text="connecting", bg=PURPLE, fg="#D9C9F5", font=("Segoe UI", 9)
        )
        self.status_label.pack(side="right", padx=12)

        self._build_rail()
        self._build_list()
        self._build_chat()

    def _build_rail(self) -> None:
        rail = tk.Frame(self.root, bg=CREAM, width=168)
        rail.grid(row=1, column=0, sticky="ns")
        rail.grid_propagate(False)

        tk.Label(
            rail, text="💬 Semaphore", bg=CREAM, fg=ORANGE,
            font=("Segoe UI", 12, "bold"),
        ).pack(pady=(18, 22), padx=14, anchor="w")

        tk.Button(
            rail, text="+  New Message", command=self._new_conversation,
            bg=AMBER, fg=INK, relief="flat", font=("Segoe UI", 9, "bold"),
            activebackground=AMBER_DEEP, cursor="hand2", pady=6,
        ).pack(fill="x", padx=14)

        for label, command in (
            ("Chats", lambda: None),
            ("New room", self._new_room),
            ("Join room", self._join_room),
            ("Contacts", self._show_contacts),
        ):
            tk.Button(
                rail, text=label, command=command, bg=CREAM, fg=INK,
                relief="flat", anchor="w", font=("Segoe UI", 9),
                activebackground=LIST_BG, cursor="hand2", pady=5,
            ).pack(fill="x", padx=14, pady=(10, 0))

        self.me_label = tk.Label(
            rail, text="", bg=CREAM, fg=MUTED, font=("Segoe UI", 8), anchor="w"
        )
        self.me_label.pack(side="bottom", fill="x", padx=14, pady=12)

    def _build_list(self) -> None:
        panel = tk.Frame(self.root, bg=LIST_BG, width=210)
        panel.grid(row=1, column=1, sticky="ns")
        panel.grid_propagate(False)

        self.conversations = tk.Listbox(
            panel, bg=LIST_BG, fg=INK, relief="flat", highlightthickness=0,
            font=("Segoe UI", 9), selectbackground=AMBER, selectforeground=INK,
            activestyle="none",
        )
        self.conversations.pack(fill="both", expand=True, padx=8, pady=8)
        self.conversations.bind("<<ListboxSelect>>", self._on_pick)
        self._keys: list[str] = []

    def _build_chat(self) -> None:
        pane = tk.Frame(self.root, bg=AMBER)
        pane.grid(row=1, column=2, sticky="nsew")
        pane.rowconfigure(1, weight=1)
        pane.columnconfigure(0, weight=1)

        header = tk.Frame(pane, bg=AMBER_DEEP, height=40)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        self.peer_label = tk.Label(
            header, text="  no conversation selected", bg=AMBER_DEEP, fg=INK,
            font=("Segoe UI", 10, "bold"), anchor="w",
        )
        self.peer_label.pack(side="left", fill="y")
        self.typing_label = tk.Label(
            header, text="", bg=AMBER_DEEP, fg="#6B5836", font=("Segoe UI", 8, "italic")
        )
        self.typing_label.pack(side="right", padx=12)

        self.transcript = tk.Text(
            pane, bg=AMBER, relief="flat", highlightthickness=0, wrap="word",
            font=("Segoe UI", 10), padx=16, pady=12, state="disabled", cursor="arrow",
        )
        self.transcript.grid(row=1, column=0, sticky="nsew")

        # Tk has no rounded bubbles. Justification, a light background and
        # generous spacing get close enough to read as one.
        self.transcript.tag_configure(
            "theirs", background=BUBBLE, foreground=INK, justify="left",
            lmargin1=8, lmargin2=8, rmargin=140, spacing1=4, spacing3=8, borderwidth=6,
            relief="flat",
        )
        self.transcript.tag_configure(
            "mine", background=BUBBLE_MINE, foreground=INK, justify="right",
            lmargin1=140, rmargin=8, spacing1=4, spacing3=8, borderwidth=6, relief="flat",
        )
        self.transcript.tag_configure(
            "who", foreground=MUTED, font=("Segoe UI", 8), spacing1=6
        )
        self.transcript.tag_configure(
            "system", foreground="#7A6743", font=("Segoe UI", 8, "italic"), justify="center",
            spacing1=6, spacing3=6,
        )

        composer = tk.Frame(pane, bg=AMBER)
        composer.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        composer.columnconfigure(0, weight=1)

        self.entry = ttk.Entry(composer, font=("Segoe UI", 10))
        self.entry.grid(row=0, column=0, sticky="ew", ipady=5)
        self.entry.bind("<Return>", self._on_send)
        self.entry.bind("<Key>", self._on_key)

        self.send_button = tk.Button(
            composer, text="Send", command=self._on_send, bg=PURPLE, fg="white",
            relief="flat", font=("Segoe UI", 9, "bold"), cursor="hand2", padx=18,
        )
        self.send_button.grid(row=0, column=1, padx=(8, 0))
        self._set_composer(enabled=False)

    # -------------------------------------------------------------- the loop ---

    def run(self) -> None:
        self.root.after(POLL_MS, self._poll)
        self._refresh_list()
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
                    self._system(f"{type(exc).__name__}: {exc}")
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
            self._system("not sent -- pick a conversation first")

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

    def _on_pick(self, _event: object) -> None:
        selection = self.conversations.curselection()
        if selection:
            self.controller.select(self._keys[selection[0]])

    def _new_conversation(self) -> None:
        who = simpledialog.askstring("New message", "Who do you want to talk to?", parent=self.root)
        if who and who.strip():
            self.controller.select(who.strip())

    def _new_room(self) -> None:
        room = simpledialog.askstring("New room", "Room name:", parent=self.root)
        if room and room.strip():
            self.controller.create_room(self._hashed(room))

    def _join_room(self) -> None:
        room = simpledialog.askstring("Join room", "Room name:", parent=self.root)
        if room and room.strip():
            self.controller.join(self._hashed(room))

    @staticmethod
    def _hashed(room: str) -> str:
        room = room.strip()
        return room if room.startswith("#") else f"#{room}"

    def _show_contacts(self) -> None:
        online = self.model.online_users()
        messagebox.showinfo(
            "Contacts",
            "\n".join(online) if online else "Nobody else is online.",
            parent=self.root,
        )

    # ------------------------------------------------------------- rendering ---

    def _render(self, event: Event) -> None:
        """The model's observer. Only ever called from the main thread."""
        if isinstance(event, MessageAdded):
            if event.conversation == self.model.active:
                self._append(event.message)
            self._refresh_list()
        elif isinstance(event, ConversationSelected):
            self._redraw()
        elif isinstance(event, HistoryLoaded):
            if event.conversation == self.model.active:
                self._redraw()
        elif isinstance(event, UnreadChanged | RoomMembersChanged | RosterReplaced):
            self._refresh_list()
        elif isinstance(event, PresenceChanged):
            self._refresh_list()
            self._system(f"{event.user} is {'online' if event.online else 'offline'}")
        elif isinstance(event, TypingChanged):
            if event.conversation == self.model.active and event.users:
                who = ", ".join(event.users)
                self.typing_label.config(text=f"{who} is typing…")
            else:
                self.typing_label.config(text="")
        elif isinstance(event, ConnectionStateChanged):
            self.status_label.config(text=event.state.lower())
            self._set_composer(enabled=event.state == "ONLINE")
            self.me_label.config(text=f"signed in as {self.model.username or '?'}")
        elif isinstance(event, ErrorRaised):
            self._system(f"{event.code}: {event.message}")

    def _set_composer(self, *, enabled: bool) -> None:
        """The composer is enabled in exactly one connection state."""
        state = "normal" if enabled else "disabled"
        self.entry.config(state=state)
        self.send_button.config(state=state)

    def _refresh_list(self) -> None:
        keys = self.model.keys()
        self._keys = keys
        self.conversations.delete(0, "end")
        for key in keys:
            conversation = self.model.conversation(key)
            mark = "●" if self.model.is_online(key) else ("#" if key.startswith("#") else "○")
            unread = f"  ({conversation.unread})" if conversation.unread else ""
            self.conversations.insert("end", f" {mark} {key}{unread}")
            if key == self.model.active:
                self.conversations.selection_clear(0, "end")
                self.conversations.selection_set("end")

    def _redraw(self) -> None:
        active = self.model.active
        self.peer_label.config(text=f"  {active}" if active else "  no conversation selected")
        self.typing_label.config(text="")

        self.transcript.config(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.config(state="disabled")

        if active is None:
            return
        for message in self.model.conversation(active).messages:
            self._append(message)
        self._refresh_list()

    def _append(self, message: Message) -> None:
        self.transcript.config(state="normal")
        if not message.mine:
            self.transcript.insert("end", f"{message.sender}\n", "who")
        self.transcript.insert("end", f"{message.body}\n", "mine" if message.mine else "theirs")
        self.transcript.config(state="disabled")
        self.transcript.see("end")

    def _system(self, text: str) -> None:
        self.transcript.config(state="normal")
        self.transcript.insert("end", f"{text}\n", "system")
        self.transcript.config(state="disabled")
        self.transcript.see("end")
