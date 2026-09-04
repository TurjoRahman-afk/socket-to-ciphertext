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
from im.server.registries import RoomRegistry, Session, SessionRegistry
from im.server.store.users import InMemoryUsers

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
        users: InMemoryUsers,  # who has an account
    ) -> None:
        # live connections. Is Alice online right now ? and how do i reach her ?
        self.sessions = sessions
        # membership by name
        self.rooms = rooms
        # accounts, survives disconnects. "Does alice exists and is this her password"
        self.users = users

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
        else:
            session.send(error("UNSUPPORTED", f"{frame.type} arrives in a later phase"))

    def on_disconnect(self, session: Session) -> None:
        """Called by the connection when its socket closes, for any reason."""
        username = session.username
        if username is None:
            return  # Never logged in, so nobody was ever told they arrived.

        # Read the membership before forgetting it, or there is nobody left to
        # tell that this person has gone.
        rooms = self.rooms.rooms_of(username)

        self.sessions.logout(username)
        self.rooms.forget(username)
        session.username = None

        self._announce(username, OFFLINE)
        for room in rooms:
            self._broadcast_room_state(room)

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
        if not self.users.register(username, pass_hash):
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
        if recipient is None:
            # Queuing for an offline user is phase 5. Until then, say so
            # plainly rather than accepting a message that goes nowhere.
            session.send(error("USER_OFFLINE", f"{target} is not online"))
            return False
        recipient.send(outgoing)
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

        for name in sorted(members):
            if name == session.username:
                continue  # The sender already has their own message.
            member = self.sessions.get(name)
            if member is not None:
                member.send(outgoing)
        return True

    # ------------------------------------------------------------ presence ---

    def _announce(self, username: str, state: str) -> None:
        """Tell everyone else that a user arrived or left."""
        announcement = Frame(
            type=MessageType.PRESENCE,
            data={"user": username, "state": state},
        )
        for other in self.sessions.others(username):
            other.send(announcement)
