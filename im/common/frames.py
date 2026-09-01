"""The Frame dataclass and the MessageType enum -- the vocabulary of the wire.

Both sides of the connection import this module, so it must not depend on
anything else in the project beyond `ids`. See docs/protocol.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from im.common.ids import new_id, now_ms

PROTOCOL_VERSION = 1


class ProtocolError(Exception):
    """A frame that cannot be understood.

    Raised while decoding. The server answers with an ERROR frame and closes
    the connection rather than guessing what the peer meant.
    """


class MessageType(StrEnum):
    """Every frame type. The value is what travels on the wire."""

    # Client to server
    REGISTER = "REGISTER"
    LOGIN = "LOGIN"
    GET_KEY = "GET_KEY"
    MSG = "MSG"
    CREATE_ROOM = "CREATE_ROOM"
    JOIN = "JOIN"
    LEAVE = "LEAVE"
    TYPING = "TYPING"
    HISTORY = "HISTORY"
    PING = "PING"

    # Server to client
    OK = "OK"
    ERROR = "ERROR"
    LOGIN_OK = "LOGIN_OK"
    KEY = "KEY"
    ACK = "ACK"
    ROOM_STATE = "ROOM_STATE"
    HISTORY_RESULT = "HISTORY_RESULT"
    PONG = "PONG"
    PRESENCE = "PRESENCE"


# Wire names that map to a differently spelled attribute on Frame.
#   "from" is a Python keyword, so the attribute is `sender`.
#   "n" is short because a nonce rides on every single message.
_WIRE_TO_ATTR = {"from": "sender", "n": "nonce"}
_ATTR_TO_WIRE = {v: k for k, v in _WIRE_TO_ATTR.items()}

# Keys the Frame owns. Anything else in a frame lands in `data`.
_RESERVED = {"v", "id", "type", "ts", "from", "to", "body", "n"}


@dataclass(slots=True)
class Frame:
    """One protocol message.

    The named fields are the ones every frame may carry. Type-specific payload
    (a username, a room, a public key) goes in `data` and is flattened to the
    top level on the wire, so a frame stays one flat JSON object.
    """

    type: MessageType
    sender: str | None = None
    to: str | None = None
    body: str | None = None
    nonce: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_id)
    ts: int = field(default_factory=now_ms)
    v: int = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        """The JSON-ready form. Fields that are None are left out entirely."""
        out: dict[str, Any] = {"v": self.v, "id": self.id, "type": str(self.type), "ts": self.ts}
        for attr in ("sender", "to", "body", "nonce"):
            value = getattr(self, attr)
            if value is not None:
                out[_ATTR_TO_WIRE.get(attr, attr)] = value
        for key, value in self.data.items():
            if key in _RESERVED:
                raise ProtocolError(f"data key {key!r} collides with a reserved field")
            out[key] = value
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Frame:
        """Rebuild a Frame from a decoded JSON object, validating as it goes."""
        if not isinstance(raw, dict):
            raise ProtocolError("frame must be a JSON object")

        version = raw.get("v", PROTOCOL_VERSION)
        if version != PROTOCOL_VERSION:
            raise ProtocolError(f"unsupported protocol version {version!r}")

        raw_type = raw.get("type")
        if raw_type is None:
            raise ProtocolError("frame has no type")
        try:
            message_type = MessageType(raw_type)
        except ValueError:
            raise ProtocolError(f"unknown frame type {raw_type!r}") from None

        ts = raw.get("ts", now_ms())
        if not isinstance(ts, int) or isinstance(ts, bool):
            raise ProtocolError("ts must be an integer")

        return cls(
            type=message_type,
            sender=raw.get("from"),
            to=raw.get("to"),
            body=raw.get("body"),
            nonce=raw.get("n"),
            data={k: v for k, v in raw.items() if k not in _RESERVED},
            id=raw.get("id") or new_id(),
            ts=ts,
            v=version,
        )


def error(code: str, message: str) -> Frame:
    """An ERROR frame. Every rejection path builds one of these."""
    return Frame(type=MessageType.ERROR, data={"code": code, "message": message})
