"""One conversation: its messages and how many are unread.

A conversation is keyed by whoever is at the other end -- a username for a
direct chat, or a room name beginning with '#'. The model holds several of
these at once, which is the slide's "multiple conversations at the same time"
requirement.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Message:
    """One line of chat, as a view needs it.

    Deliberately not a Frame. A Frame is a wire format with a version, a
    nonce and a recipient; this is what gets drawn. Keeping them apart is
    what stops protocol changes rippling into the view.
    """

    id: str
    sender: str
    body: str
    ts: int
    mine: bool = False


@dataclass(slots=True)
class Conversation:
    key: str
    messages: list[Message] = field(default_factory=list)
    unread: int = 0

    @property
    def is_room(self) -> bool:
        return self.key.startswith("#")

    def add(self, message: Message, *, active: bool) -> None:
        """Append a message. It counts as unread unless this chat is on screen.

        Your own messages never count: you have just typed them.
        """
        self.messages.append(message)
        if not active and not message.mine:
            self.unread += 1

    def mark_read(self) -> bool:
        """Clear the unread count. True if it actually changed."""
        if self.unread == 0:
            return False
        self.unread = 0
        return True

    def last(self) -> Message | None:
        return self.messages[-1] if self.messages else None

    def __len__(self) -> int:
        return len(self.messages)
