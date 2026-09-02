"""MessageRouter tests. Phase 2.

Not one socket in this file. The router reaches connections through the
Session protocol, so a fake that appends to a list is a complete stand-in.
"""

from __future__ import annotations

import pytest

from im.common.frames import Frame, MessageType
from im.server.registries import RoomRegistry, SessionRegistry
from im.server.router import MessageRouter
from im.server.store.users import InMemoryUsers

HASH = "sha256-of-hunter2"


class FakeSession:
    """A Session that remembers what it was sent."""

    def __init__(self) -> None:
        self.username: str | None = None
        self.outbox: list[Frame] = []

    def send(self, frame: Frame) -> None:
        self.outbox.append(frame)

    @property
    def types(self) -> list[MessageType]:
        return [f.type for f in self.outbox]

    def last(self) -> Frame:
        return self.outbox[-1]


@pytest.fixture
def router() -> MessageRouter:
    return MessageRouter(SessionRegistry(), RoomRegistry(), InMemoryUsers())


def sign_up(router: MessageRouter, session: FakeSession, user: str) -> None:
    router.handle(session, Frame(type=MessageType.REGISTER, data={"user": user, "pass_hash": HASH}))


def log_in(router: MessageRouter, session: FakeSession, user: str) -> None:
    router.handle(session, Frame(type=MessageType.LOGIN, data={"user": user, "pass_hash": HASH}))


def online(router: MessageRouter, user: str) -> FakeSession:
    session = FakeSession()
    sign_up(router, session, user)
    log_in(router, session, user)
    session.outbox.clear()
    return session


def quiet(*sessions: FakeSession) -> None:
    """Forget the setup traffic.

    Bringing users online makes them announce each other, so everyone already
    holds a few PRESENCE frames before a test does anything. Clear them, or
    every assertion has to account for who logged in after whom.
    """
    for session in sessions:
        session.outbox.clear()


# ---------------------------------------------------------------- accounts ---


def test_register_then_login(router: MessageRouter) -> None:
    session = FakeSession()
    sign_up(router, session, "alice")
    assert session.types == [MessageType.OK]

    log_in(router, session, "alice")
    assert session.last().type is MessageType.LOGIN_OK
    assert session.username == "alice"


def test_a_taken_username_is_refused(router: MessageRouter) -> None:
    sign_up(router, FakeSession(), "alice")
    second = FakeSession()
    sign_up(router, second, "alice")
    assert second.last().data["code"] == "USER_EXISTS"


def test_a_wrong_password_is_refused(router: MessageRouter) -> None:
    session = FakeSession()
    sign_up(router, session, "alice")
    router.handle(
        session, Frame(type=MessageType.LOGIN, data={"user": "alice", "pass_hash": "wrong"})
    )
    assert session.last().data["code"] == "BAD_CREDENTIALS"
    assert session.username is None


def test_an_unknown_user_gets_the_same_error_as_a_wrong_password(router: MessageRouter) -> None:
    """Two different replies would let anyone enumerate who has an account."""
    session = FakeSession()
    log_in(router, session, "nobody")
    assert session.last().data["code"] == "BAD_CREDENTIALS"


def test_one_username_cannot_be_online_twice(router: MessageRouter) -> None:
    online(router, "alice")
    second = FakeSession()
    sign_up(router, second, "alice")
    log_in(router, second, "alice")
    assert second.last().data["code"] == "ALREADY_ONLINE"


def test_nothing_works_before_logging_in(router: MessageRouter) -> None:
    session = FakeSession()
    router.handle(session, Frame(type=MessageType.MSG, to="bob", body="hi"))
    assert session.last().data["code"] == "NOT_LOGGED_IN"


def test_ping_works_without_logging_in(router: MessageRouter) -> None:
    session = FakeSession()
    router.handle(session, Frame(type=MessageType.PING))
    assert session.types == [MessageType.PONG]


def test_login_ok_carries_the_roster(router: MessageRouter) -> None:
    online(router, "alice")
    bob = FakeSession()
    sign_up(router, bob, "bob")
    log_in(router, bob, "bob")

    login_ok = next(f for f in bob.outbox if f.type is MessageType.LOGIN_OK)
    assert login_ok.data["roster"] == ["alice"]


# ---------------------------------------------------------------- delivery ---


def test_a_message_reaches_only_its_recipient(router: MessageRouter) -> None:
    alice = online(router, "alice")
    bob = online(router, "bob")
    carol = online(router, "carol")
    quiet(alice, bob, carol)

    router.handle(alice, Frame(type=MessageType.MSG, to="bob", body="just for you"))

    assert [f.type for f in bob.outbox] == [MessageType.MSG]
    assert bob.last().body == "just for you"
    assert bob.last().sender == "alice"
    assert carol.outbox == []
    assert alice.types == [MessageType.ACK]


def test_the_sender_cannot_forge_who_a_message_is_from(router: MessageRouter) -> None:
    """`from` is set by the server from the authenticated session."""
    alice = online(router, "alice")
    bob = online(router, "bob")

    router.handle(alice, Frame(type=MessageType.MSG, sender="carol", to="bob", body="spoofed"))

    assert bob.last().sender == "alice"


def test_the_ack_names_the_message_it_acknowledges(router: MessageRouter) -> None:
    alice = online(router, "alice")
    online(router, "bob")
    sent = Frame(type=MessageType.MSG, to="bob", body="hi")

    router.handle(alice, sent)

    assert alice.last().data["ref"] == sent.id


def test_messaging_someone_offline_says_so(router: MessageRouter) -> None:
    alice = online(router, "alice")
    router.handle(alice, Frame(type=MessageType.MSG, to="bob", body="anyone there"))
    assert alice.last().data["code"] == "USER_OFFLINE"


def test_a_message_needs_a_recipient(router: MessageRouter) -> None:
    alice = online(router, "alice")
    router.handle(alice, Frame(type=MessageType.MSG, body="to nobody"))
    assert alice.last().data["code"] == "NO_RECIPIENT"


def test_a_room_message_reaches_every_member_but_the_sender(router: MessageRouter) -> None:
    alice = online(router, "alice")
    bob = online(router, "bob")
    carol = online(router, "carol")
    dave = online(router, "dave")
    for name in ("alice", "bob", "carol"):
        router.rooms.join("#general", name)
    quiet(alice, bob, carol, dave)

    router.handle(alice, Frame(type=MessageType.MSG, to="#general", body="hello room"))

    assert bob.last().body == "hello room"
    assert carol.last().body == "hello room"
    assert dave.outbox == []
    assert alice.types == [MessageType.ACK]


def test_an_unknown_room_is_refused(router: MessageRouter) -> None:
    alice = online(router, "alice")
    router.handle(alice, Frame(type=MessageType.MSG, to="#nowhere", body="hello"))
    assert alice.last().data["code"] == "NO_SUCH_ROOM"


# ---------------------------------------------------------------- presence ---


def test_logging_in_announces_you_to_everyone_else(router: MessageRouter) -> None:
    alice = online(router, "alice")
    online(router, "bob")

    announcement = alice.last()
    assert announcement.type is MessageType.PRESENCE
    assert announcement.data == {"user": "bob", "state": "ONLINE"}


def test_you_do_not_get_your_own_presence(router: MessageRouter) -> None:
    bob = online(router, "bob")
    assert bob.outbox == []


def test_disconnecting_announces_you_as_offline(router: MessageRouter) -> None:
    alice = online(router, "alice")
    bob = online(router, "bob")
    alice.outbox.clear()

    router.on_disconnect(bob)

    assert alice.last().data == {"user": "bob", "state": "OFFLINE"}
    assert not router.sessions.is_online("bob")


def test_disconnecting_before_login_announces_nothing(router: MessageRouter) -> None:
    alice = online(router, "alice")
    router.on_disconnect(FakeSession())
    assert alice.outbox == []


def test_disconnecting_removes_you_from_your_rooms(router: MessageRouter) -> None:
    bob = online(router, "bob")
    router.rooms.join("#general", "bob")

    router.on_disconnect(bob)

    assert router.rooms.members("#general") == set()


def test_a_name_is_free_again_after_disconnecting(router: MessageRouter) -> None:
    bob = online(router, "bob")
    router.on_disconnect(bob)

    again = FakeSession()
    log_in(router, again, "bob")

    assert again.last().type is MessageType.LOGIN_OK
