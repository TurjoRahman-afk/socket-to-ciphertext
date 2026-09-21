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
from im.crypto.envelope import DecryptionFailed

log = logging.getLogger(__name__)

ROOM_PREFIX = "#"


class ChatController:
    def __init__(self, connection, model: ChatModel, keyring=None) -> None:
        self.connection = connection
        self.model = model
        # None means plaintext bodies. Encryption is a layer this class
        # applies, not something the model or the view ever sees.
        self.keyring = keyring
        # Outgoing text waiting on a public key that has been asked for
        # but has not arrived yet.
        self._held: dict[str, list[str]] = {}
        # Inbound encrypted frames waiting on the sender's key. A message
        # can arrive from somebody we have never spoken to, so the first
        # thing they say may well need a key fetch of its own.
        self._awaiting: dict[str, list[Frame]] = {}

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

        me = self.model.username or "me"

        if self.keyring is None:
            frame = self.connection.message(target, text)
        elif target.startswith(ROOM_PREFIX):
            frame = self._send_to_room(target, text, me)
            if frame is None:
                return None
        else:
            sealed = self.keyring.seal(target, text, me)
            if sealed is None:
                # No key yet. Ask for one and hold the message rather than
                # sending it in the clear, which would silently break the
                # promise the whole phase exists to make.
                self._hold(target, text)
                return None
            ciphertext, nonce = sealed
            frame = self.connection.message(target, ciphertext, nonce=nonce)

        # The model always holds plaintext. A view renders what was typed,
        # not what went on the wire.
        message = Message(id=frame.id, sender=me, body=text, ts=frame.ts, mine=True)
        self.model.add_message(target, message)
        return message

    def _send_to_room(self, room: str, text: str, me: str) -> Frame | None:
        """Seal one room message once per member, or hold it until we can.

        A frame carries one body and a room has one key per member, so the
        ciphertexts travel beside the frame rather than in it.
        """
        members = [name for name in self.model.room_members(room)]
        missing = self.keyring.missing_keys([name for name in members if name != me])
        if missing:
            # Every member or none. Sending to the ones whose keys we happen
            # to hold would drop the rest out of the conversation silently.
            self._hold(room, text, fetch=missing)
            return None

        envelopes = self.keyring.seal_for_members(members, text, me)
        if envelopes is None:
            self._hold(room, text, fetch=members)
            return None
        return self.connection.message(room, "", envelopes=envelopes)

    def _hold(self, target: str, text: str, fetch: list[str] | None = None) -> None:
        """Queue a message until the keys needed to seal it arrive.

        `fetch` names whose keys to ask for, which for a room is every member
        rather than the room itself -- a room has no key of its own.
        """
        first = target not in self._held
        self._held.setdefault(target, []).append(text)
        if first:
            for user in fetch if fetch is not None else [target]:
                self.connection.get_key(user)

    def _release(self, user: str) -> None:
        """Send whatever was waiting on this person's key."""
        waiting = self._held.pop(user, [])
        if not waiting:
            return
        previous, self.model.active = self.model.active, user
        try:
            for text in waiting:
                self.send(text)
        finally:
            self.model.active = previous

    def select(self, key: str) -> None:
        """Put a conversation on screen, and tell the other side it was read.

        Only what is actually unread is reported. Re-reporting the whole
        conversation every time it is opened would send one receipt per
        message per glance.
        """
        unread = [
            m
            for m in self.model.conversation(key).messages
            if not m.mine and self.model.conversation(key).unread
        ]
        self.model.select(key)

        if key.startswith(ROOM_PREFIX):
            return
        for message in unread:
            try:
                self.connection.receipt(message.sender, message.id, "READ")
            except Exception:  # noqa: BLE001 -- see _file
                log.debug("could not send a read receipt")

    def ping(self) -> None:
        self.connection.ping()

    def typing(self, on: bool = True) -> None:
        """Report composing in the conversation on screen, if there is one."""
        if self.model.active is not None:
            self.connection.typing(self.model.active, on)

    def request_history(self, key: str | None = None, limit: int = 50) -> bool:
        """Ask the server for scrollback on a conversation."""
        target = key or self.model.active
        if target is None:
            return False
        self.connection.history(target, limit=limit)
        return True

    def create_room(self, room: str, members: list[str] | None = None) -> None:
        self.connection.create_room(room, members)

    def invite(self, room: str, members: list[str]) -> None:
        self.connection.invite(room, members)

    def join(self, room: str) -> None:
        self.connection.join(room)

    def leave(self, room: str) -> None:
        self.connection.leave(room)

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
        elif frame.type is MessageType.TYPING:
            self._typing(frame)
        elif frame.type is MessageType.RECEIPT:
            self._receipt(frame)
        elif frame.type is MessageType.KEY:
            self._key(frame)
        elif frame.type is MessageType.HISTORY_RESULT:
            self._history_result(frame)
        elif frame.type is MessageType.ROOM_STATE:
            self._room_state(frame)
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

        # An encrypted message from somebody whose key we do not hold yet
        # cannot be read. Ask for the key and keep the frame rather than
        # showing the user an error they can do nothing about.
        # Whose key opens this? For a direct message it is the person who
        # sent it, which is also the conversation. For a room it is still the
        # sender -- the room has no key of its own.
        peer = frame.sender or key

        if (
            self.keyring is not None
            and frame.nonce
            and peer
            and peer != self.model.username
            and not self.keyring.knows(peer)
        ):
            first = peer not in self._awaiting
            self._awaiting.setdefault(peer, []).append(frame)
            if first:
                self.connection.get_key(peer)
            return

        self._file(frame, key)

    def _file(self, frame: Frame, key: str) -> None:
        """Put one inbound message into the model, decrypting if needed."""
        # Report delivery as soon as it is in the model, not when it is drawn.
        # A view that is slow to paint has still received the message, and
        # tying the receipt to rendering would make it a lie on a busy client.
        if frame.sender and not key.startswith(ROOM_PREFIX):
            try:
                self.connection.receipt(frame.sender, frame.id, "DELIVERED")
            except Exception:  # noqa: BLE001 -- a receipt is never worth failing a message over
                log.debug("could not send a delivery receipt")

        self.model.add_message(
            key,
            Message(
                id=frame.id,
                sender=frame.sender or "?",
                body=self._plaintext(frame, frame.sender or key),
                ts=frame.ts or now_ms(),
                mine=False,
            ),
        )

    def _receipt(self, frame: Frame) -> None:
        """A message of ours arrived at, or was read by, the other end."""
        ref = frame.data.get("ref")
        state = frame.data.get("state")
        who = frame.sender
        if ref and state and who:
            self.model.set_receipt(str(who), str(ref), str(state))

    def _key(self, frame: Frame) -> None:
        """A public key arrived. Remember it and send anything held for them."""
        user = frame.data.get("user")
        pubkey = frame.data.get("pubkey")
        if not user or not pubkey or self.keyring is None:
            return
        self.keyring.remember(str(user), str(pubkey))
        self._release(str(user))
        self._deliver_awaiting(str(user))

        # A room was waiting on *every* member's key, so the last one to
        # arrive is what unblocks it -- and it does not arrive under the
        # room's name.
        for room in [key for key in self._held if key.startswith(ROOM_PREFIX)]:
            if not self.keyring.missing_keys(
                [name for name in self.model.room_members(room) if name != self.model.username]
            ):
                self._release(room)

    def _deliver_awaiting(self, user: str) -> None:
        """Decrypt and show what arrived before we had this person's key."""
        for frame in self._awaiting.pop(user, []):
            target = frame.to or ""
            key = target if target.startswith(ROOM_PREFIX) else user
            self._file(frame, key)

    def _plaintext(self, frame: Frame, peer: str) -> str:
        """The readable body of an inbound message.

        A frame with a nonce is encrypted. One that fails to decrypt is
        reported in place rather than dropped -- a message the user cannot
        read is something they need to know about, not something to hide.
        """
        body = frame.body or ""
        if self.keyring is None or not frame.nonce:
            return body
        try:
            return self.keyring.open(peer, body, frame.nonce, frame.sender or "")
        except DecryptionFailed:
            log.warning("could not decrypt a message from %s", peer)
            return "[could not decrypt this message]"

    def _presence(self, frame: Frame) -> None:
        user = frame.data.get("user")
        if not user:
            return
        self.model.set_presence(str(user), frame.data.get("state") == "ONLINE")

    def _typing(self, frame: Frame) -> None:
        """A room hint belongs to the room; a direct one to its sender."""
        sender = frame.sender
        target = frame.to or ""
        if not sender:
            return
        key = target if target.startswith(ROOM_PREFIX) else sender
        self.model.set_typing(key, sender, bool(frame.data.get("on")))

    def _history_result(self, frame: Frame) -> None:
        """Turn stored rows back into messages a view can draw.

        `mine` is recomputed from who we are rather than stored, because the
        server has no idea which client is asking.
        """
        key = frame.data.get("room") or frame.to
        if not key:
            return

        me = self.model.username
        self.model.load_history(
            str(key),
            [
                Message(
                    id=str(row.get("id", "")),
                    sender=str(row.get("from", "?")),
                    body=str(row.get("body") or ""),
                    ts=int(row.get("ts") or now_ms()),
                    mine=row.get("from") == me,
                )
                for row in frame.data.get("messages") or []
            ],
        )

    def _room_state(self, frame: Frame) -> None:
        room = frame.data.get("room") or frame.to
        if not room:
            return
        members = frame.data.get("members") or []
        self.model.set_room_members(str(room), [str(name) for name in members])

    def _logged_in(self, frame: Frame) -> None:
        username = frame.data.get("user")
        if username:
            self.model.set_identity(str(username))
        roster = frame.data.get("roster") or []
        self.model.replace_roster([str(name) for name in roster])
