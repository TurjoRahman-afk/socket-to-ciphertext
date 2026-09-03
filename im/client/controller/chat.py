"""ChatController -- gestures in, frames out; frames in, model updates out.

The only place that knows both the protocol and the model. The connection
below it deals in Frames and knows nothing about conversations; the model
above it deals in conversations and knows nothing about Frames. This is the
translation layer between them, and it is where the two vocabularies meet.

Nothing here touches a socket directly either -- it calls methods on whatever
connection object it is given, which is why its tests pass a fake.
"""

from __future__ import annotations

import logging

from im.client.model.chat import ChatModel
from im.client.model.conversation import Message
from im.common.frames import Frame, MessageType
from im.common.ids import now_ms

log = logging.getLogger(__name__)

ROOM_PREFIX = "#"


class ChatController:
    def __init__(self, connection, model: ChatModel) -> None:
        self.connection = connection
        self.model = model

    # ---------------------------------------------------- gestures -> frames ---

    def send(self, text: str) -> Message | None:
        """Send what the user typed to the conversation on screen.

        The message is added to the model immediately rather than when the
        server acknowledges it. Waiting for the ACK would make your own
        messages appear a round trip late, which reads as lag.
        """
        target = self.model.active
        if target is None or not text.strip():
            return None

        frame = self.connection.message(target, text)
        message = Message(
            id=frame.id,
            sender=self.model.username or "me",
            body=text,
            ts=frame.ts,
            mine=True,
        )
        self.model.add_message(target, message)
        return message

    def select(self, key: str) -> None:
        self.model.select(key)

    def ping(self) -> None:
        self.connection.ping()

    # ---------------------------------------------------- frames -> the model ---

    def on_frame(self, frame: Frame) -> None:
        """Translate one inbound frame into model changes.

        Called from whichever thread drains the inbound queue -- never
        directly from the reader thread, because the model is not thread
        safe by design.
        """
        if frame.type is MessageType.MSG:
            self._incoming_message(frame)
        elif frame.type is MessageType.PRESENCE:
            self._presence(frame)
        elif frame.type is MessageType.LOGIN_OK:
            self._logged_in(frame)
        elif frame.type is MessageType.ERROR:
            self.model.raise_error(
                str(frame.data.get("code", "ERROR")),
                str(frame.data.get("message", "")),
            )
        elif frame.type in (MessageType.ACK, MessageType.PONG, MessageType.OK):
            pass  # Nothing for a view to show yet.
        else:
            log.debug("no handler for %s", frame.type)

    def on_state(self, state: str) -> None:
        self.model.set_connection_state(str(state))

    # ---------------------------------------------------------------- private ---

    def _incoming_message(self, frame: Frame) -> None:
        """Work out which conversation a message belongs to.

        For a room it is the room, and for a direct message it is the person
        who sent it -- never the recipient, which is us.
        """
        target = frame.to or ""
        key = target if target.startswith(ROOM_PREFIX) else (frame.sender or target)
        if not key:
            log.warning("dropping a MSG with nobody to attribute it to")
            return

        self.model.add_message(
            key,
            Message(
                id=frame.id,
                sender=frame.sender or "?",
                body=frame.body or "",
                ts=frame.ts or now_ms(),
                mine=False,
            ),
        )

    def _presence(self, frame: Frame) -> None:
        user = frame.data.get("user")
        if not user:
            return
        self.model.set_presence(str(user), frame.data.get("state") == "ONLINE")

    def _logged_in(self, frame: Frame) -> None:
        username = frame.data.get("user")
        if username:
            self.model.set_identity(str(username))
        roster = frame.data.get("roster") or []
        self.model.replace_roster([str(name) for name in roster])
