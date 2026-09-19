"""MessageRouter -- deciding who receives what.

Pure logic. This module imports no socket, no ssl and no threading: it reaches
a connection only through the Session protocol, whose send() drops a frame on
a queue. That is what lets every routing rule below be tested with a fake
session that appends to a list, with no network involved and no port to bind.

The server can therefore be wrong about sockets or wrong about routing, but
never confusingly wrong about both at once.
"""

from __future__ import annotations

import logging

from im.common.frames import Frame, MessageType, error
from im.common.ids import now_ms
from im.server.registries import RoomRegistry, Session, SessionRegistry
from im.server.store.messages import DELIVERED, READ, MessageStore, direct_conversation
from im.server.store.users import SqliteUsers

log = logging.getLogger(__name__)

#: Room names are distinguished from usernames by a leading marker, so one
#: `to` field can address either without a second field to say which.
ROOM_PREFIX = "#"

ONLINE = "ONLINE"
OFFLINE = "OFFLINE"


class MessageRouter:
    def __init__(
        self,
        sessions: SessionRegistry,  # who is online right now
        rooms: RoomRegistry,  # who is in which room
        users: SqliteUsers,  # who has an account
        messages: MessageStore | None = None,  # history and offline queue
    ) -> None:
        # live connections. Is Alice online right now ? and how do i reach her ?
        self.sessions = sessions
        # membership by name
        self.rooms = rooms
        # accounts, survives disconnects. "Does alice exists and is this her password"
        self.users = users
        # history, and what is owed to people who are not here
        self.messages = messages

    # ------------------------------------------------------------ inbound ---

    def handle(self, session: Session, frame: Frame) -> None:
        """Act on one frame from one connection."""
        if frame.type is MessageType.PING:
            session.send(Frame(type=MessageType.PONG))
            return
        if frame.type is MessageType.REGISTER:
            self._register(session, frame)
            return
        if frame.type is MessageType.LOGIN:
            self._login(session, frame)
            return

        # Everything past this point needs to know who is asking.
        if session.username is None:
            session.send(error("NOT_LOGGED_IN", "log in before sending anything else"))
            return

        if frame.type is MessageType.MSG:
            self._message(session, frame)
        elif frame.type is MessageType.CREATE_ROOM:
            self._create_room(session, frame)
        elif frame.type is MessageType.JOIN:
            self._join(session, frame)
        elif frame.type is MessageType.LEAVE:
            self._leave(session, frame)
        elif frame.type is MessageType.TYPING:
            self._typing(session, frame)
        elif frame.type is MessageType.RECEIPT:
            self._receipt(session, frame)
        elif frame.type is MessageType.HISTORY:
            self._history(session, frame)
        elif frame.type is MessageType.GET_KEY:
            self._get_key(session, frame)
        else:
            session.send(error("UNSUPPORTED", f"{frame.type} arrives in a later phase"))

    def on_disconnect(self, session: Session) -> None:
        """Called by the connection when its socket closes, for any reason."""
        username = session.username
        if username is None:
            return  # Never logged in, so nobody was ever told they arrived.

        self.sessions.logout(username)
        session.username = None

        # Membership is deliberately NOT dropped. You are in a room until you
        # LEAVE it, connected or not -- which is what makes LOGIN_OK's room
        # list meaningful and lets a room message wait for an absent member.
        # PRESENCE already tells the room that this person has gone offline.
        self._announce(username, OFFLINE)

    # ------------------------------------------------------------ accounts ---

    def _register(self, session: Session, frame: Frame) -> None:
        username = frame.data.get("user")
        pass_hash = frame.data.get("pass_hash")
        if not username or not pass_hash:
            session.send(error("BAD_REGISTER", "user and pass_hash are required"))
            return
        if username.startswith(ROOM_PREFIX):
            session.send(error("BAD_USERNAME", f"a username may not start with {ROOM_PREFIX}"))
            return
        # The public key is optional so that a client built before phase 6
        # still works. Without one, nobody can encrypt to this user.
        pubkey = frame.data.get("pubkey")
        if not self.users.register(username, pass_hash, pubkey=pubkey):
            session.send(error("USER_EXISTS", f"{username} is taken"))
            return

        log.info("registered %s", username)
        session.send(Frame(type=MessageType.OK, data={"user": username}))

    def _login(self, session: Session, frame: Frame) -> None:
        if session.username is not None:
            session.send(
                error("ALREADY_LOGGED_IN", f"this connection is already {session.username}")
            )
            return

        username = frame.data.get("user")
        pass_hash = frame.data.get("pass_hash")
        if not username or not pass_hash:
            session.send(error("BAD_LOGIN", "user and pass_hash are required"))
            return

        if not self.users.verify(username, pass_hash):
            # One message for both an unknown user and a wrong password: two
            # different replies would let anyone enumerate who has an account.
            session.send(error("BAD_CREDENTIALS", "unknown user or wrong password"))
            return

        if not self.sessions.login(username, session):
            session.send(error("ALREADY_ONLINE", f"{username} is connected from elsewhere"))
            return

        session.username = username
        log.info("%s logged in", username)

        # The roster is who else is here *now*, which is why it is read after
        # the login above rather than before it.
        session.send(
            Frame(
                type=MessageType.LOGIN_OK,
                to=username,
                data={
                    "user": username,
                    "roster": [u for u in self.sessions.usernames() if u != username],
                    "rooms": self.rooms.rooms_of(username),
                },
            )
        )
        self._announce(username, ONLINE)

        # After LOGIN_OK, so the client already knows who it is and has its
        # roster before messages start arriving.
        self._flush_pending(session, username)

    def _get_key(self, session: Session, frame: Frame) -> None:
        """Hand out somebody's public key.

        Public by definition -- it is what everyone needs in order to encrypt
        to this person, and it reveals nothing. The private half never leaves
        the client that generated it and never appears in this database.

        An account with no key and a name with no account both answer NO_KEY.
        A different reply for each would turn this into a way of discovering
        who has registered.
        """
        user = frame.data.get("user")
        if not user:
            session.send(error("NO_RECIPIENT", "GET_KEY needs a user"))
            return

        pubkey = self.users.pubkey(str(user))
        if pubkey is None:
            session.send(error("NO_KEY", f"no public key is published for {user}"))
            return

        session.send(
            Frame(type=MessageType.KEY, data={"user": str(user), "pubkey": pubkey})
        )

    # --------------------------------------------------------------- rooms ---

    def _create_room(self, session: Session, frame: Frame) -> None:
        room = self._room_name(session, frame)
        if room is None:
            return
        if not self.rooms.create(room):
            session.send(error("ROOM_EXISTS", f"{room} already exists -- JOIN it instead"))
            return

        # Creating a room puts you in it. Creating one you are not a member of
        # would be a strange thing to want.
        self.rooms.join(room, session.username)
        log.info("%s created %s", session.username, room)
        self._broadcast_room_state(room)

    def _join(self, session: Session, frame: Frame) -> None:
        room = self._room_name(session, frame)
        if room is None:
            return
        if not self.rooms.exists(room):
            # Deliberately not created on the fly: a typo would otherwise put
            # you alone in a room you think other people are already in.
            session.send(error("NO_SUCH_ROOM", f"{room} does not exist -- CREATE_ROOM first"))
            return

        self.rooms.join(room, session.username)
        self._broadcast_room_state(room)

    def _leave(self, session: Session, frame: Frame) -> None:
        room = self._room_name(session, frame)
        if room is None:
            return
        if session.username not in self.rooms.members(room):
            session.send(error("NOT_A_MEMBER", f"you are not in {room}"))
            return

        self.rooms.leave(room, session.username)
        # Tell the room first, then the person who left -- they are no longer
        # a member, so the broadcast will not reach them.
        self._broadcast_room_state(room)
        session.send(self._room_state(room))

    def _room_name(self, session: Session, frame: Frame) -> str | None:
        """Validate the `room` field, answering with an error if it is wrong."""
        room = frame.data.get("room")
        if not room or not isinstance(room, str):
            session.send(error("BAD_ROOM", "a room name is required"))
            return None
        if not room.startswith(ROOM_PREFIX):
            session.send(error("BAD_ROOM", f"a room name must start with {ROOM_PREFIX}"))
            return None
        if len(room) < 2 or len(room) > 32 or any(c.isspace() for c in room):
            session.send(error("BAD_ROOM", "a room name is 2-32 characters and has no spaces"))
            return None
        return room

    def _room_state(self, room: str) -> Frame:
        return Frame(
            type=MessageType.ROOM_STATE,
            to=room,
            data={"room": room, "members": sorted(self.rooms.members(room))},
        )

    def _broadcast_room_state(self, room: str) -> None:
        """Tell every member who is in the room now.

        Sent to the whole room rather than only to whoever joined, so that
        everybody's member list stays correct without polling.
        """
        state = self._room_state(room)
        for name in sorted(self.rooms.members(room)):
            member = self.sessions.get(name)
            if member is not None:
                member.send(state)

    # ------------------------------------------------------------ delivery ---

    def _message(self, session: Session, frame: Frame) -> None:
        target = frame.to
        if not target:
            session.send(error("NO_RECIPIENT", "MSG needs a 'to'"))
            return

        # Rebuilt rather than forwarded: `from` is set by the server from the
        # authenticated session, so a client cannot claim to be someone else.
        # The id and timestamp are kept so sender and recipient agree on them.
        outgoing = Frame(
            type=MessageType.MSG,
            sender=session.username,  # from the session, not from the frame
            to=target,
            body=frame.body,
            nonce=frame.nonce,
            id=frame.id,
            ts=frame.ts,
        )

        if target.startswith(ROOM_PREFIX):
            delivered = self._to_room(session, target, outgoing)
        else:
            delivered = self._to_user(session, target, outgoing)

        if delivered:
            # "ref", not "id": every frame already has its own id, and a data
            # key may not shadow a reserved field.
            session.send(Frame(type=MessageType.ACK, data={"ref": frame.id}))

    def _to_user(self, session: Session, target: str, outgoing: Frame) -> bool:
        recipient = self.sessions.get(target)

        # Refused before anything is written. A message to a name nobody owns
        # -- a typo, usually -- should not end up in the database at all.
        if recipient is None and (self.messages is None or not self.users.exists(target)):
            session.send(error("USER_OFFLINE", f"{target} is not online"))
            return False

        sender = session.username or ""
        self._record(outgoing, direct_conversation(sender, target), target)

        if recipient is not None:
            recipient.send(outgoing)
        else:
            self.messages.queue_for(target, outgoing.id)
            log.info("queued a message for %s, who is offline", target)
        return True

    def _to_room(self, session: Session, room: str, outgoing: Frame) -> bool:
        if not self.rooms.exists(room):
            session.send(error("NO_SUCH_ROOM", f"{room} does not exist"))
            return False

        members = self.rooms.members(room)
        if session.username not in members:
            # Otherwise anyone could shout into any room they could name,
            # without ever appearing in its member list.
            session.send(error("NOT_A_MEMBER", f"join {room} before sending to it"))
            return False

        self._record(outgoing, room, room)

        for name in sorted(members):
            if name == session.username:
                continue  # The sender already has their own message.
            member = self.sessions.get(name)
            if member is not None:
                member.send(outgoing)
            elif self.messages is not None:
                # Stored once above; this only notes who still owes a
                # delivery, so a room of five costs one row per absentee
                # rather than five copies of the message.
                self.messages.queue_for(name, outgoing.id)
        return True

    def _record(self, outgoing: Frame, conversation: str, recipient: str) -> None:
        if self.messages is None:
            return
        self.messages.record(
            message_id=outgoing.id,
            conversation=conversation,
            sender=outgoing.sender or "",
            recipient=recipient,
            body=outgoing.body,
            nonce=outgoing.nonce,
            ts=outgoing.ts,
        )

    # ------------------------------------------------------------- history ---

    def _history(self, session: Session, frame: Frame) -> None:
        """Answer a request for scrollback.

        The client names the conversation the way it sees it -- a username or
        a room -- and the server turns that into the stored key.
        """
        if self.messages is None:
            session.send(error("UNSUPPORTED", "this server keeps no history"))
            return

        target = frame.data.get("room") or frame.to
        if not target:
            session.send(error("NO_RECIPIENT", "HISTORY needs a conversation"))
            return

        target = str(target)
        if target.startswith(ROOM_PREFIX):
            if session.username not in self.rooms.members(target):
                session.send(error("NOT_A_MEMBER", f"join {target} to read its history"))
                return
            conversation = target
        else:
            conversation = direct_conversation(session.username or "", target)

        before = frame.data.get("before")
        limit = frame.data.get("limit")
        rows = self.messages.history(
            conversation,
            before=int(before) if before else None,
            limit=int(limit) if limit else 50,
        )

        session.send(
            Frame(
                type=MessageType.HISTORY_RESULT,
                to=target,
                data={
                    "room": target,
                    "messages": [
                        {
                            "id": row["id"],
                            "from": row["sender"],
                            # The real recipient, not the requester's view of
                            # the conversation -- the client already has that
                            # from the "room" field beside this list.
                            "to": row["recipient"],
                            "body": row["body"],
                            "n": row["nonce"],
                            "ts": row["ts"],
                            "state": (
                                READ
                                if row["read_at"]
                                else (DELIVERED if row["delivered_at"] else "SENT")
                            ),
                        }
                        for row in rows
                    ],
                },
            )
        )

    def _flush_pending(self, session: Session, username: str) -> None:
        """Hand over everything that arrived while this user was away."""
        if self.messages is None:
            return
        for row in self.messages.flush(username):
            session.send(
                Frame(
                    type=MessageType.MSG,
                    sender=row["sender"],
                    to=row["recipient"],
                    body=row["body"],
                    nonce=row["nonce"],
                    id=row["id"],
                    ts=row["ts"],
                )
            )

    # ------------------------------------------------------------ receipts ---

    def _receipt(self, session: Session, frame: Frame) -> None:
        """Record that a message arrived or was read, and tell its sender.

        Only the recipient of a message may report on it. Without that check
        anybody who learned a message id could claim somebody else had read
        it, which is a small lie the sender has no way to detect.
        """
        if self.messages is None:
            return

        ref = frame.data.get("ref")
        state = frame.data.get("state")
        if not ref or state not in (DELIVERED, READ):
            session.send(error("BAD_RECEIPT", "a receipt needs a ref and a state"))
            return

        sender = self.messages.mark(str(ref), str(state), now_ms())
        if sender is None or sender == session.username:
            # Unknown message, or somebody reporting on their own -- neither
            # is worth an error, and neither has anyone to notify.
            return

        recipient = self.sessions.get(sender)
        if recipient is None:
            # The sender is away. The state is stored, so HISTORY will carry
            # it when they come back; there is no need to queue the receipt
            # itself.
            return

        recipient.send(
            Frame(
                type=MessageType.RECEIPT,
                sender=session.username,
                to=sender,
                data={"ref": str(ref), "state": str(state)},
            )
        )

    # -------------------------------------------------------------- typing ---

    def _typing(self, session: Session, frame: Frame) -> None:
        """Relay a typing hint. Nothing is stored and nothing is acknowledged.

        Every failure here is silent on purpose. A typing indicator is a hint,
        not a message: answering an unroutable one with an ERROR would spend a
        round trip telling the user something they do not need to know, and
        would make the composer flash errors as they type.
        """
        target = frame.to
        if not target:
            return

        relayed = Frame(
            type=MessageType.TYPING,
            sender=session.username,
            to=target,
            data={"on": bool(frame.data.get("on"))},
        )

        if target.startswith(ROOM_PREFIX):
            members = self.rooms.members(target)
            if session.username not in members:
                return
            recipients = [name for name in sorted(members) if name != session.username]
        else:
            recipients = [target]

        for name in recipients:
            recipient = self.sessions.get(name)
            if recipient is not None:
                recipient.send(relayed)

    # ------------------------------------------------------------ presence ---

    def _announce(self, username: str, state: str) -> None:
        """Tell everyone else that a user arrived or left."""
        announcement = Frame(
            type=MessageType.PRESENCE,
            data={"user": username, "state": state},
        )
        for other in self.sessions.others(username):
            other.send(announcement)
