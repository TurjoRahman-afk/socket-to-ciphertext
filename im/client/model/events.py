"""What the model tells its views when something changes.

Frozen dataclasses rather than bare tuples or dicts, so a view can match on
type and a typo in a field name is caught before it renders wrong.

These carry no Frame, no socket and no widget. A view receiving MessageAdded
knows what to draw without knowing anything about the protocol -- which is
what allows the console view and the Tk view to be swapped for each other.
"""

from __future__ import annotations

from dataclasses import dataclass

from im.client.model.conversation import Message


@dataclass(frozen=True, slots=True)
class IdentityEstablished:
    """We are logged in and know who we are."""

    username: str


@dataclass(frozen=True, slots=True)
class RosterReplaced:
    """The whole contact list, as it was at login."""

    users: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PresenceChanged:
    user: str
    online: bool


@dataclass(frozen=True, slots=True)
class MessageAdded:
    conversation: str
    message: Message


@dataclass(frozen=True, slots=True)
class UnreadChanged:
    conversation: str
    unread: int


@dataclass(frozen=True, slots=True)
class RoomMembersChanged:
    """Who is in a room now. Sent by the server on every join and leave, so a
    view never has to ask."""

    room: str
    members: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConversationSelected:
    conversation: str | None


@dataclass(frozen=True, slots=True)
class ConnectionStateChanged:
    """Carried as a plain string on purpose.

    The model must not import anything from im.client.net, or it stops being
    testable without the transport. ConnectionState is a StrEnum, so passing
    its value across costs nothing.
    """

    state: str


@dataclass(frozen=True, slots=True)
class ErrorRaised:
    code: str
    message: str


#: Anything the model may emit. A view narrows on this.
Event = (
    IdentityEstablished
    | RosterReplaced
    | PresenceChanged
    | MessageAdded
    | UnreadChanged
    | RoomMembersChanged
    | ConversationSelected
    | ConnectionStateChanged
    | ErrorRaised
)
