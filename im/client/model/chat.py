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
from dataclasses import replace

from im.client.model.conversation import Conversation, Message, SearchHit
from im.client.model.events import (
    ConnectionStateChanged,
    ContactsChanged,
    ConversationSelected,
    ErrorRaised,
    Event,
    HistoryLoaded,
    IdentityEstablished,
    MessageAdded,
    PresenceChanged,
    ReceiptChanged,
    RoomMembersChanged,
    RosterReplaced,
    TypingChanged,
    UnreadChanged,
)

Listener = Callable[[Event], None]


class ChatModel:
    # this creates the initial state
    # this is the begining moment
    def __init__(self) -> None:
        self.username: str | None = None
        self.connection_state: str = "DISCONNECTED"
        # Presence: who is connected this second.
        self.roster: dict[str, bool] = {}
        # Contacts: who this person knows, online or not. Kept apart from the
        # roster on purpose -- conflating them is what made the contact list
        # empty itself on every restart.
        self.contacts: dict[str, bool] = {}
        self.conversations: dict[str, Conversation] = {}
        self.rooms: dict[str, tuple[str, ...]] = {}
        self.typing: dict[str, set[str]] = {}
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

    def replace_contacts(self, contacts: list[dict]) -> None:
        """The contact list as the server last reported it."""
        fresh = {
            str(entry.get("user")): bool(entry.get("online"))
            for entry in contacts
            if entry.get("user")
        }
        if fresh == self.contacts:
            return
        self.contacts = fresh
        # A contact who is online is also on the roster, so presence stays in
        # one place and the two cannot disagree.
        for name, online in fresh.items():
            self.roster.setdefault(name, online)
        self._emit(ContactsChanged(tuple(sorted(self.contacts))))

    def contact_names(self) -> list[str]:
        """The people this person has kept, online first.

        Contacts only. Not the roster -- being online is not the same as
        being known, and folding the two together listed strangers in your
        contact list purely because they happened to be connected.

        Messaging somebody adds them server-side, so anyone you actually talk
        to turns up here without a separate gesture.
        """
        me = self.username
        names = [name for name in self.contacts if name and name != me]
        return sorted(names, key=lambda name: (not self.is_online(name), name.lower()))

    def set_presence(self, user: str, online: bool) -> None:
        if user in self.contacts:
            self.contacts[user] = online
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

        # Sending a message ends typing. Without this a client that goes
        # quiet after pressing Enter would leave "bob is typing" on screen
        # forever, since nothing else clears it.
        if not message.mine:
            self.set_typing(key, message.sender, False)

        self._emit(MessageAdded(key, message))
        if conversation.unread != before:
            self._emit(UnreadChanged(key, conversation.unread))

    def load_history(self, key: str, messages: list[Message]) -> None:
        """Replace a conversation's messages with scrollback from the server.

        Replaced rather than merged: the server has stored everything either
        side sent, so it is the authority on what was said and in what order.
        Merging would mean reconciling two orderings for no gain.

        Unread counts are untouched. Reading history is not the same as
        reading the messages -- the user asked to look backwards, which says
        nothing about whether they have seen what arrived while they were
        looking somewhere else.
        """
        conversation = self.conversation(key)
        conversation.messages = list(messages)
        self._emit(HistoryLoaded(key, len(messages)))

    def set_receipt(self, key: str, message_id: str, state: str) -> None:
        """Move one of your own messages to DELIVERED or READ.

        Messages are frozen, so this replaces the one in the list. Receipts
        never move backwards: a delivery receipt arriving after a read one --
        which happens when the two cross on the wire -- is ignored.
        """
        order = {"SENT": 0, "DELIVERED": 1, "READ": 2}
        conversation = self.conversations.get(key)
        if conversation is None:
            return

        for index, message in enumerate(conversation.messages):
            if message.id != message_id:
                continue
            if order.get(state, 0) <= order.get(message.state, 0):
                return
            conversation.messages[index] = replace(message, state=state)
            self._emit(ReceiptChanged(key, message_id, state))
            return

    def select(self, key: str | None) -> None:
        """Put a conversation on screen, which also marks it read."""
        self.active = key
        self._emit(ConversationSelected(key))
        if key is not None and self.conversation(key).mark_read():
            self._emit(UnreadChanged(key, 0))

    def unread_total(self) -> int:
        return sum(c.unread for c in self.conversations.values())

    # ---------------------------------------------------------------- search ---

    def search(self, query: str, limit: int = 100) -> list[SearchHit]:
        """Every loaded message containing `query`, newest first.

        Deliberately client-side. The server cannot do this: since room
        messages are sealed per member and direct messages per pair, what it
        stores is ciphertext it holds no key for. Searching it would mean
        either giving the server the keys or giving up the encryption, and
        the point of the encryption is that neither happens.

        The cost is that only what this client has loaded is searchable --
        request history for a conversation first and it becomes so. That
        trade is inherent to end-to-end encryption, not an oversight.
        """
        needle = query.strip().casefold()
        if not needle:
            return []

        hits = [
            SearchHit(key=key, message=message)
            for key, conversation in self.conversations.items()
            for message in conversation.messages
            if needle in message.body.casefold()
        ]
        hits.sort(key=lambda hit: hit.message.ts, reverse=True)
        return hits[:limit]

    def keys(self) -> list[str]:
        """Open conversations, rooms first so they do not get lost in a list
        of names."""
        return sorted(self.conversations, key=lambda k: (not k.startswith("#"), k.lower()))

    # ---------------------------------------------------------------- typing ---

    def set_typing(self, conversation: str, user: str, typing: bool) -> None:
        """Record that somebody started or stopped typing.

        A set per conversation, because several people can be typing in a room
        at once. Nothing here is persisted -- it is a hint about right now.
        """
        who = self.typing.setdefault(conversation, set())
        before = frozenset(who)

        if typing:
            who.add(user)
        else:
            who.discard(user)

        if frozenset(who) != before:
            self._emit(TypingChanged(conversation, tuple(sorted(who))))

    def typing_in(self, conversation: str) -> tuple[str, ...]:
        return tuple(sorted(self.typing.get(conversation, ())))

    # ----------------------------------------------------------------- rooms ---

    def set_room_members(self, room: str, members: list[str]) -> None:
        """Record who is in a room, as the server last reported it."""
        ordered = tuple(sorted(members))
        if self.rooms.get(room) == ordered:
            return
        self.rooms[room] = ordered
        self.conversation(room)  # so a joined room shows up in the chat list
        self._emit(RoomMembersChanged(room, ordered))

    def room_members(self, room: str) -> tuple[str, ...]:
        return self.rooms.get(room, ())

    def my_rooms(self) -> list[str]:
        """Rooms this user is currently in.

        Derived from membership rather than tracked separately, so there is no
        second copy of the truth to fall out of step with the server.
        """
        if self.username is None:
            return []
        return sorted(room for room, members in self.rooms.items() if self.username in members)

    # ---------------------------------------------------------------- errors ---

    def raise_error(self, code: str, message: str) -> None:
        """Surface a server refusal. The model records nothing -- an error is
        something to show, not state to keep."""
        self._emit(ErrorRaised(code, message))
