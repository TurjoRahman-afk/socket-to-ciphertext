"""ChatModel -- everything the user interface needs to know, and nothing else.

The centre of the client. Conversations, the roster, unread counts and the
connection state live here, and views learn about changes by subscribing.

Three rules hold this file's value:

  - It imports nothing from im.client.net and nothing from im.client.view. No
    sockets, no widgets, no protocol. tests/test_model_has_no_tkinter.py
    fails the build if that ever stops being true.
  - It is driven from ONE thread. There is no lock here on purpose: the
    caller serialises access instead -- the console view drains a queue, and
    the Tk view will drain the same queue from root.after(). A lock would
    hide that requirement rather than satisfy it, because a view that read
    half a conversation while it was being appended to would still tear.
  - Every mutation emits an event. A view that renders on events alone never
    has to poll, and a second view can be attached without touching this file.
"""

from __future__ import annotations

from collections.abc import Callable

from im.client.model.conversation import Conversation, Message
from im.client.model.events import (
    ConnectionStateChanged,
    ConversationSelected,
    ErrorRaised,
    Event,
    IdentityEstablished,
    MessageAdded,
    PresenceChanged,
    RosterReplaced,
    UnreadChanged,
)

Listener = Callable[[Event], None]


class ChatModel:
    # this creates the initial state
    # this is the begining moment
    def __init__(self) -> None:
        self.username: str | None = None
        self.connection_state: str = "DISCONNECTED"
        self.roster: dict[str, bool] = {}
        self.conversations: dict[str, Conversation] = {}
        self.active: str | None = None
        self._listeners: list[Listener] = []

    # ------------------------------------------------------------ observers ---

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Register a view. Returns a function that unsubscribes it."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _emit(self, event: Event) -> None:
        # Iterate a copy: a listener may unsubscribe itself while handling an
        # event, and mutating the list mid-iteration would skip the next one.
        for listener in list(self._listeners):
            listener(event)

    # ------------------------------------------------------------- identity ---

    def set_identity(self, username: str) -> None:
        self.username = username
        self._emit(IdentityEstablished(username))

    def set_connection_state(self, state: str) -> None:
        if state == self.connection_state:
            return
        self.connection_state = state
        self._emit(ConnectionStateChanged(state))

    # --------------------------------------------------------------- roster ---

    def replace_roster(self, users: list[str]) -> None:
        """Everyone who was online at login. Anyone already known but absent
        from this list is marked offline rather than forgotten -- you may
        still have their conversation open."""
        for name in users:
            self.roster[name] = True
        for name in self.roster:
            if name not in users:
                self.roster[name] = False
        self._emit(RosterReplaced(tuple(sorted(self.roster))))

    def set_presence(self, user: str, online: bool) -> None:
        if self.roster.get(user) is online:
            return
        self.roster[user] = online
        self._emit(PresenceChanged(user, online))

    def is_online(self, user: str) -> bool:
        return self.roster.get(user, False)

    def online_users(self) -> list[str]:
        return sorted(name for name, online in self.roster.items() if online)

    # -------------------------------------------------------- conversations ---

    def conversation(self, key: str) -> Conversation:
        """Fetch a conversation, creating it the first time it is needed.

        Messages can arrive from someone you have never spoken to, so this
        cannot require the conversation to exist already.
        """
        existing = self.conversations.get(key)
        if existing is None:
            existing = Conversation(key)
            self.conversations[key] = existing
        return existing

    def add_message(self, key: str, message: Message) -> None:
        conversation = self.conversation(key)
        before = conversation.unread
        conversation.add(message, active=(key == self.active))

        self._emit(MessageAdded(key, message))
        if conversation.unread != before:
            self._emit(UnreadChanged(key, conversation.unread))

    def select(self, key: str | None) -> None:
        """Put a conversation on screen, which also marks it read."""
        self.active = key
        self._emit(ConversationSelected(key))
        if key is not None and self.conversation(key).mark_read():
            self._emit(UnreadChanged(key, 0))

    def unread_total(self) -> int:
        return sum(c.unread for c in self.conversations.values())

    def keys(self) -> list[str]:
        """Open conversations, rooms first so they do not get lost in a list
        of names."""
        return sorted(self.conversations, key=lambda k: (not k.startswith("#"), k.lower()))

    # ---------------------------------------------------------------- errors ---

    def raise_error(self, code: str, message: str) -> None:
        """Surface a server refusal. The model records nothing -- an error is
        something to show, not state to keep."""
        self._emit(ErrorRaised(code, message))
