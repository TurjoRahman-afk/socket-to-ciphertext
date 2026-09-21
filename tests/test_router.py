"""MessageRouter tests. Phase 2.

Not one socket in this file. The router reaches connections through the
Session protocol, so a fake that appends to a list is a complete stand-in.
"""

from __future__ import annotations

import pytest

from im.common.frames import Frame, MessageType
from im.server.registries import RoomRegistry, SessionRegistry
from im.server.router import MessageRouter
from im.server.store.db import Database
from im.server.store.messages import MessageStore
from im.server.store.users import SqliteContacts, SqliteUsers

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
    # scrypt_n is dropped to the minimum here on purpose. These tests log in
    # dozens of times and the real cost factor would add minutes; the
    # derivation itself is exercised in tests/test_store.py.
    users = SqliteUsers(Database(), scrypt_n=2)
    return MessageRouter(SessionRegistry(), RoomRegistry(), users)


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


# ------------------------------------------------------------------ typing ---


def typing(router: MessageRouter, session: FakeSession, to: str, on: bool = True) -> None:
    router.handle(session, Frame(type=MessageType.TYPING, to=to, data={"on": on}))


def test_typing_is_relayed_to_the_recipient(router: MessageRouter) -> None:
    alice = online(router, "alice")
    bob = online(router, "bob")
    quiet(alice, bob)

    typing(router, alice, "bob")

    assert bob.last().type is MessageType.TYPING
    assert bob.last().sender == "alice"
    assert bob.last().data == {"on": True}


def test_typing_is_never_acknowledged(router: MessageRouter) -> None:
    """It is a hint, not a message. An ACK would double the traffic."""
    alice = online(router, "alice")
    online(router, "bob")
    quiet(alice)

    typing(router, alice, "bob")

    assert alice.outbox == []


def test_typing_to_a_room_reaches_the_other_members(router: MessageRouter) -> None:
    alice = online(router, "alice")
    bob = online(router, "bob")
    carol = online(router, "carol")
    for name in ("alice", "bob", "carol"):
        router.rooms.join("#general", name)
    quiet(alice, bob, carol)

    typing(router, alice, "#general")

    assert bob.last().type is MessageType.TYPING
    assert carol.last().type is MessageType.TYPING
    assert alice.outbox == []


def test_typing_into_a_room_you_are_not_in_is_dropped_silently(router: MessageRouter) -> None:
    """Answering with an ERROR would make the composer flash as you type."""
    alice = online(router, "alice")
    bob = online(router, "bob")
    router.rooms.join("#general", "bob")
    quiet(alice, bob)

    typing(router, alice, "#general")

    assert alice.outbox == []
    assert bob.outbox == []


def test_typing_at_somebody_offline_is_dropped_silently(router: MessageRouter) -> None:
    alice = online(router, "alice")
    quiet(alice)

    typing(router, alice, "nobody")

    assert alice.outbox == []


def test_typing_off_is_relayed_too(router: MessageRouter) -> None:
    alice = online(router, "alice")
    bob = online(router, "bob")
    quiet(alice, bob)

    typing(router, alice, "bob", on=False)

    assert bob.last().data == {"on": False}


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


def test_membership_outlives_the_connection(router: MessageRouter) -> None:
    """Otherwise LOGIN_OK could never carry a room list, and a room message
    could never wait for a member who happens to be away."""
    bob = online(router, "bob")
    router.rooms.join("#general", "bob")

    router.on_disconnect(bob)

    assert router.rooms.members("#general") == {"bob"}

    again = FakeSession()
    log_in(router, again, "bob")
    login_ok = next(f for f in again.outbox if f.type is MessageType.LOGIN_OK)
    assert login_ok.data["rooms"] == ["#general"]


def test_a_name_is_free_again_after_disconnecting(router: MessageRouter) -> None:
    bob = online(router, "bob")
    router.on_disconnect(bob)

    again = FakeSession()
    log_in(router, again, "bob")

    assert again.last().type is MessageType.LOGIN_OK


# ------------------------------------------------------------------- rooms ---


def create(router: MessageRouter, session: FakeSession, room: str) -> None:
    router.handle(session, Frame(type=MessageType.CREATE_ROOM, data={"room": room}))


def join(router: MessageRouter, session: FakeSession, room: str) -> None:
    router.handle(session, Frame(type=MessageType.JOIN, data={"room": room}))


def leave(router: MessageRouter, session: FakeSession, room: str) -> None:
    router.handle(session, Frame(type=MessageType.LEAVE, data={"room": room}))


def test_creating_a_room_puts_you_in_it(router: MessageRouter) -> None:
    alice = online(router, "alice")

    create(router, alice, "#general")

    assert router.rooms.members("#general") == {"alice"}
    state = alice.last()
    assert state.type is MessageType.ROOM_STATE
    assert state.data == {"room": "#general", "members": ["alice"]}


def test_a_room_cannot_be_created_twice(router: MessageRouter) -> None:
    alice = online(router, "alice")
    create(router, alice, "#general")
    bob = online(router, "bob")

    create(router, bob, "#general")

    assert bob.last().data["code"] == "ROOM_EXISTS"


def test_joining_tells_everyone_already_in_the_room(router: MessageRouter) -> None:
    """So member lists stay correct without anybody polling."""
    alice = online(router, "alice")
    bob = online(router, "bob")
    create(router, alice, "#general")
    quiet(alice, bob)

    join(router, bob, "#general")

    assert alice.last().data == {"room": "#general", "members": ["alice", "bob"]}
    assert bob.last().data == {"room": "#general", "members": ["alice", "bob"]}


def test_joining_a_room_that_does_not_exist_is_refused(router: MessageRouter) -> None:
    """A typo would otherwise put you alone in a room you believe is busy."""
    alice = online(router, "alice")

    join(router, alice, "#typo")

    assert alice.last().data["code"] == "NO_SUCH_ROOM"
    assert not router.rooms.exists("#typo")


def test_leaving_removes_you_and_tells_the_room(router: MessageRouter) -> None:
    alice = online(router, "alice")
    bob = online(router, "bob")
    create(router, alice, "#general")
    join(router, bob, "#general")
    quiet(alice, bob)

    leave(router, bob, "#general")

    assert router.rooms.members("#general") == {"alice"}
    assert alice.last().data["members"] == ["alice"]
    # Bob is told too, even though the broadcast no longer reaches him.
    assert bob.last().data["members"] == ["alice"]


def test_leaving_a_room_you_are_not_in_is_refused(router: MessageRouter) -> None:
    alice = online(router, "alice")
    create(router, alice, "#general")
    bob = online(router, "bob")

    leave(router, bob, "#general")

    assert bob.last().data["code"] == "NOT_A_MEMBER"


@pytest.mark.parametrize("name", ["general", "", "#", "#a room", "#" + "x" * 40, None, 42])
def test_a_bad_room_name_is_refused(router: MessageRouter, name) -> None:
    alice = online(router, "alice")

    router.handle(alice, Frame(type=MessageType.CREATE_ROOM, data={"room": name}))

    assert alice.last().data["code"] == "BAD_ROOM"


def test_you_must_join_a_room_before_sending_to_it(router: MessageRouter) -> None:
    """Otherwise anyone could shout into any room they could name."""
    alice = online(router, "alice")
    create(router, alice, "#general")
    bob = online(router, "bob")

    router.handle(bob, Frame(type=MessageType.MSG, to="#general", body="barging in"))

    assert bob.last().data["code"] == "NOT_A_MEMBER"


def test_disconnecting_announces_presence_not_a_room_change(router: MessageRouter) -> None:
    """Going offline is not leaving. The room still lists them, and PRESENCE
    is what tells everybody they are not reachable."""
    alice = online(router, "alice")
    bob = online(router, "bob")
    create(router, alice, "#general")
    join(router, bob, "#general")
    quiet(alice, bob)

    router.on_disconnect(bob)

    assert alice.last().type is MessageType.PRESENCE
    assert alice.last().data == {"user": "bob", "state": "OFFLINE"}
    assert not [f for f in alice.outbox if f.type is MessageType.ROOM_STATE]


def test_login_ok_lists_the_rooms_you_are_in(router: MessageRouter) -> None:
    alice = online(router, "alice")
    create(router, alice, "#general")
    create(router, alice, "#random")

    fresh = FakeSession()
    sign_up(router, fresh, "bob")
    join(router, alice, "#general")  # no-op, alice is already in
    log_in(router, fresh, "bob")

    login_ok = next(f for f in fresh.outbox if f.type is MessageType.LOGIN_OK)
    assert login_ok.data["rooms"] == []  # bob is in none of them yet


# ----------------------------------------------------- history and offline ---


@pytest.fixture
def stored() -> MessageRouter:
    """A router that keeps history, unlike the fixture above."""
    database = Database()
    return MessageRouter(
        SessionRegistry(),
        RoomRegistry(),
        SqliteUsers(database, scrypt_n=2),
        MessageStore(database),
    )


def history(router: MessageRouter, session: FakeSession, room: str, **extra) -> None:
    router.handle(session, Frame(type=MessageType.HISTORY, data={"room": room, **extra}))


def test_a_message_to_someone_offline_is_queued_not_refused(stored: MessageRouter) -> None:
    alice = online(stored, "alice")
    sign_up(stored, FakeSession(), "bob")  # bob has an account but is not here
    quiet(alice)

    stored.handle(alice, Frame(type=MessageType.MSG, to="bob", body="catch you later"))

    assert alice.types == [MessageType.ACK], "the sender is told it was accepted"
    assert stored.messages.pending_count("bob") == 1


def test_a_message_to_a_name_with_no_account_is_still_refused(stored: MessageRouter) -> None:
    """Queuing for a name nobody owns would fill the table with rubbish."""
    alice = online(stored, "alice")
    quiet(alice)

    stored.handle(alice, Frame(type=MessageType.MSG, to="nobody", body="hello?"))

    assert alice.last().data["code"] == "USER_OFFLINE"


def test_queued_messages_arrive_at_the_next_login(stored: MessageRouter) -> None:
    """The phase 5 exit criteria: sent while away, delivered on return."""
    alice = online(stored, "alice")
    sign_up(stored, FakeSession(), "bob")
    stored.handle(alice, Frame(type=MessageType.MSG, to="bob", body="one"))
    stored.handle(alice, Frame(type=MessageType.MSG, to="bob", body="two"))

    bob = FakeSession()
    log_in(stored, bob, "bob")

    delivered = [f for f in bob.outbox if f.type is MessageType.MSG]
    assert [f.body for f in delivered] == ["one", "two"]
    assert stored.messages.pending_count("bob") == 0


def test_queued_messages_arrive_after_login_ok(stored: MessageRouter) -> None:
    """A client needs to know who it is before messages start landing."""
    alice = online(stored, "alice")
    sign_up(stored, FakeSession(), "bob")
    stored.handle(alice, Frame(type=MessageType.MSG, to="bob", body="one"))

    bob = FakeSession()
    log_in(stored, bob, "bob")

    kinds = [f.type for f in bob.outbox]
    assert kinds.index(MessageType.LOGIN_OK) < kinds.index(MessageType.MSG)


def test_a_room_message_is_queued_for_absent_members(stored: MessageRouter) -> None:
    alice = online(stored, "alice")
    sign_up(stored, FakeSession(), "bob")
    stored.rooms.join("#general", "alice")
    stored.rooms.join("#general", "bob")

    stored.handle(alice, Frame(type=MessageType.MSG, to="#general", body="anyone about"))

    assert len(stored.messages) == 1, "stored once, not copied per member"
    assert stored.messages.pending_count("bob") == 1


def test_history_returns_what_was_said(stored: MessageRouter) -> None:
    alice = online(stored, "alice")
    bob = online(stored, "bob")
    stored.handle(alice, Frame(type=MessageType.MSG, to="bob", body="first"))
    stored.handle(bob, Frame(type=MessageType.MSG, to="alice", body="second"))
    quiet(alice)

    history(stored, alice, "bob")

    result = alice.last()
    assert result.type is MessageType.HISTORY_RESULT
    assert [m["body"] for m in result.data["messages"]] == ["first", "second"]
    assert [m["from"] for m in result.data["messages"]] == ["alice", "bob"]


def test_history_is_the_same_conversation_from_either_side(stored: MessageRouter) -> None:
    alice = online(stored, "alice")
    bob = online(stored, "bob")
    stored.handle(alice, Frame(type=MessageType.MSG, to="bob", body="hello"))
    quiet(alice, bob)

    history(stored, alice, "bob")
    history(stored, bob, "alice")

    assert alice.last().data["messages"] == bob.last().data["messages"]


def test_room_history_needs_membership(stored: MessageRouter) -> None:
    alice = online(stored, "alice")
    stored.rooms.join("#general", "alice")
    stored.handle(alice, Frame(type=MessageType.MSG, to="#general", body="private"))
    bob = online(stored, "bob")
    quiet(bob)

    history(stored, bob, "#general")

    assert bob.last().data["code"] == "NOT_A_MEMBER"


def test_a_server_with_no_store_says_so(router: MessageRouter) -> None:
    alice = online(router, "alice")
    quiet(alice)
    history(router, alice, "bob")
    assert alice.last().data["code"] == "UNSUPPORTED"


# ------------------------------------------------------------------- keys ---


def test_a_public_key_given_at_registration_can_be_fetched(router: MessageRouter) -> None:
    session = FakeSession()
    router.handle(
        session,
        Frame(
            type=MessageType.REGISTER,
            data={"user": "alice", "pass_hash": HASH, "pubkey": "BASE64-ALICE"},
        ),
    )
    bob = online(router, "bob")
    quiet(bob)

    router.handle(bob, Frame(type=MessageType.GET_KEY, data={"user": "alice"}))

    assert bob.last().type is MessageType.KEY
    assert bob.last().data == {"user": "alice", "pubkey": "BASE64-ALICE"}


def test_a_user_without_a_key_answers_no_key(router: MessageRouter) -> None:
    sign_up(router, FakeSession(), "alice")  # registered, but no pubkey
    bob = online(router, "bob")
    quiet(bob)

    router.handle(bob, Frame(type=MessageType.GET_KEY, data={"user": "alice"}))

    assert bob.last().data["code"] == "NO_KEY"


def test_an_unknown_user_answers_the_same_as_a_user_without_a_key(
    router: MessageRouter,
) -> None:
    """A different reply for each would turn GET_KEY into a way of finding
    out who has registered."""
    bob = online(router, "bob")
    quiet(bob)

    router.handle(bob, Frame(type=MessageType.GET_KEY, data={"user": "nobody"}))

    assert bob.last().data["code"] == "NO_KEY"


def test_get_key_needs_a_user(router: MessageRouter) -> None:
    bob = online(router, "bob")
    quiet(bob)
    router.handle(bob, Frame(type=MessageType.GET_KEY, data={}))
    assert bob.last().data["code"] == "NO_RECIPIENT"


def test_keys_cannot_be_fetched_before_logging_in(router: MessageRouter) -> None:
    session = FakeSession()
    router.handle(session, Frame(type=MessageType.GET_KEY, data={"user": "alice"}))
    assert session.last().data["code"] == "NOT_LOGGED_IN"


# ---------------------------------------------------------------- receipts ---


def receipt(router: MessageRouter, session: FakeSession, ref: str, state: str) -> None:
    router.handle(
        session, Frame(type=MessageType.RECEIPT, data={"ref": ref, "state": state})
    )


def test_a_delivery_receipt_reaches_the_sender(stored: MessageRouter) -> None:
    alice = online(stored, "alice")
    bob = online(stored, "bob")
    sent = Frame(type=MessageType.MSG, to="bob", body="did this arrive?")
    stored.handle(alice, sent)
    quiet(alice, bob)

    receipt(stored, bob, sent.id, "DELIVERED")

    assert alice.last().type is MessageType.RECEIPT
    assert alice.last().data == {"ref": sent.id, "state": "DELIVERED"}
    assert alice.last().sender == "bob"


def test_a_read_receipt_reaches_the_sender(stored: MessageRouter) -> None:
    alice = online(stored, "alice")
    bob = online(stored, "bob")
    sent = Frame(type=MessageType.MSG, to="bob", body="read this")
    stored.handle(alice, sent)
    quiet(alice, bob)

    receipt(stored, bob, sent.id, "READ")

    assert alice.last().data["state"] == "READ"
    assert stored.messages.state_of(sent.id) == "READ"


def test_being_read_implies_having_arrived(stored: MessageRouter) -> None:
    """A delivery receipt can be lost. A read one proves both."""
    alice = online(stored, "alice")
    bob = online(stored, "bob")
    sent = Frame(type=MessageType.MSG, to="bob", body="hi")
    stored.handle(alice, sent)

    receipt(stored, bob, sent.id, "READ")

    with stored.messages.db.read() as conn:
        row = conn.execute(
            "SELECT delivered_at, read_at FROM messages WHERE id = ?", (sent.id,)
        ).fetchone()
    assert row["delivered_at"] is not None
    assert row["read_at"] is not None


def test_a_receipt_never_walks_the_state_backwards(stored: MessageRouter) -> None:
    """The two can cross on the wire, and DELIVERED must not undo READ."""
    alice = online(stored, "alice")
    bob = online(stored, "bob")
    sent = Frame(type=MessageType.MSG, to="bob", body="hi")
    stored.handle(alice, sent)

    receipt(stored, bob, sent.id, "READ")
    receipt(stored, bob, sent.id, "DELIVERED")

    assert stored.messages.state_of(sent.id) == "READ"


def test_a_receipt_for_an_unknown_message_is_ignored(stored: MessageRouter) -> None:
    bob = online(stored, "bob")
    quiet(bob)
    receipt(stored, bob, "no-such-message", "READ")
    assert bob.outbox == []


def test_a_receipt_needs_a_ref_and_a_valid_state(stored: MessageRouter) -> None:
    bob = online(stored, "bob")
    quiet(bob)

    router_handle = stored.handle
    router_handle(bob, Frame(type=MessageType.RECEIPT, data={"state": "READ"}))
    assert bob.last().data["code"] == "BAD_RECEIPT"

    router_handle(bob, Frame(type=MessageType.RECEIPT, data={"ref": "x", "state": "SEEN"}))
    assert bob.last().data["code"] == "BAD_RECEIPT"


def test_a_receipt_for_a_sender_who_is_away_is_stored_not_lost(stored: MessageRouter) -> None:
    """The state survives in history, so it shows when they come back."""
    alice = online(stored, "alice")
    bob = online(stored, "bob")
    sent = Frame(type=MessageType.MSG, to="bob", body="hi")
    stored.handle(alice, sent)
    stored.on_disconnect(alice)

    receipt(stored, bob, sent.id, "READ")

    assert stored.messages.state_of(sent.id) == "READ"


def test_history_carries_the_receipt_state(stored: MessageRouter) -> None:
    alice = online(stored, "alice")
    bob = online(stored, "bob")
    sent = Frame(type=MessageType.MSG, to="bob", body="hi")
    stored.handle(alice, sent)
    receipt(stored, bob, sent.id, "READ")
    quiet(alice)

    history(stored, alice, "bob")

    assert alice.last().data["messages"][0]["state"] == "READ"


# ---------------------------------------------------- creating with members ---


def test_a_room_can_be_created_with_members(router) -> None:
    """The design document specified CREATE_ROOM as room_name plus members."""
    faiza = online(router, "faiza")
    online(router, "aya")
    online(router, "keisha")

    router.handle(
        faiza,
        Frame(type=MessageType.CREATE_ROOM, data={"room": "#study", "members": ["aya", "keisha"]}),
    )

    assert router.rooms.members("#study") == {"faiza", "aya", "keisha"}


def test_unknown_names_are_skipped_rather_than_failing_the_room(router) -> None:
    """One misspelt name should not cost you the room."""
    faiza = online(router, "faiza")
    online(router, "aya")

    router.handle(
        faiza,
        Frame(type=MessageType.CREATE_ROOM, data={"room": "#study", "members": ["aya", "nobody"]}),
    )

    assert router.rooms.members("#study") == {"faiza", "aya"}


def test_a_room_may_not_be_added_as_a_member_of_a_room(router) -> None:
    faiza = online(router, "faiza")

    router.handle(
        faiza,
        Frame(type=MessageType.CREATE_ROOM, data={"room": "#study", "members": ["#other"]}),
    )

    assert router.rooms.members("#study") == {"faiza"}


def test_members_that_are_not_a_list_are_ignored(router) -> None:
    """A malformed frame must not raise on the connection's reader thread."""
    faiza = online(router, "faiza")

    router.handle(
        faiza, Frame(type=MessageType.CREATE_ROOM, data={"room": "#study", "members": "aya"})
    )

    assert router.rooms.members("#study") == {"faiza"}


def test_everyone_added_is_told_about_the_room(router) -> None:
    """A new member who is online learns of the room without having to ask."""
    faiza = online(router, "faiza")
    aya = online(router, "aya")
    quiet(faiza, aya)

    router.handle(
        faiza, Frame(type=MessageType.CREATE_ROOM, data={"room": "#study", "members": ["aya"]})
    )

    states = [f for f in aya.outbox if f.type is MessageType.ROOM_STATE]
    assert states and set(states[-1].data["members"]) == {"faiza", "aya"}


# ------------------------------------------------------------------- invite ---


def test_a_member_can_invite_somebody_else(router) -> None:
    faiza = online(router, "faiza")
    online(router, "aya")
    router.handle(faiza, Frame(type=MessageType.CREATE_ROOM, data={"room": "#study"}))

    router.handle(
        faiza, Frame(type=MessageType.INVITE, data={"room": "#study", "members": ["aya"]})
    )

    assert router.rooms.members("#study") == {"faiza", "aya"}


def test_a_stranger_cannot_invite_anyone(router) -> None:
    """Otherwise guessing a room name would be enough to add yourself to it."""
    faiza = online(router, "faiza")
    aya = online(router, "aya")
    router.handle(faiza, Frame(type=MessageType.CREATE_ROOM, data={"room": "#study"}))
    quiet(faiza, aya)

    router.handle(aya, Frame(type=MessageType.INVITE, data={"room": "#study", "members": ["aya"]}))

    assert router.rooms.members("#study") == {"faiza"}
    assert aya.last().data["code"] == "NOT_A_MEMBER"


def test_inviting_into_a_room_that_does_not_exist_is_an_error(router) -> None:
    faiza = online(router, "faiza")

    router.handle(
        faiza, Frame(type=MessageType.INVITE, data={"room": "#nowhere", "members": ["aya"]})
    )

    assert faiza.last().data["code"] == "NO_SUCH_ROOM"


def test_inviting_only_unknown_names_says_so(router) -> None:
    faiza = online(router, "faiza")
    router.handle(faiza, Frame(type=MessageType.CREATE_ROOM, data={"room": "#study"}))
    quiet(faiza)

    router.handle(
        faiza, Frame(type=MessageType.INVITE, data={"room": "#study", "members": ["ghost"]})
    )

    assert faiza.last().data["code"] == "NO_SUCH_USER"


# ----------------------------------------------------------------- contacts ---


@pytest.fixture
def router_with_contacts() -> MessageRouter:
    """A router that keeps contacts, which the plain fixture does not."""
    database = Database()
    users = SqliteUsers(database, scrypt_n=2)
    return MessageRouter(
        SessionRegistry(), RoomRegistry(), users, contacts=SqliteContacts(database)
    )


def test_contacts_survive_a_restart(router_with_contacts) -> None:
    """The symptom that started this: the contact list was presence, so
    closing the program emptied it."""
    router = router_with_contacts
    faiza = online(router, "faiza")
    online(router, "aya")

    router.handle(faiza, Frame(type=MessageType.ADD_CONTACT, data={"user": "aya"}))

    assert router.contacts.of("faiza") == ["aya"]


def test_login_reports_contacts_who_are_offline(router_with_contacts) -> None:
    """Being offline is a state to show, not a reason to disappear."""
    router = router_with_contacts
    faiza = online(router, "faiza")
    aya = online(router, "aya")
    router.handle(faiza, Frame(type=MessageType.ADD_CONTACT, data={"user": "aya"}))
    router.on_disconnect(aya)

    faiza.outbox.clear()
    router.on_disconnect(faiza)
    fresh = FakeSession()
    log_in(router, fresh, "faiza")

    login_ok = [f for f in fresh.outbox if f.type is MessageType.LOGIN_OK][0]
    assert login_ok.data["contacts"] == [{"user": "aya", "online": False}]


def test_messaging_somebody_adds_them_both_ways(router_with_contacts) -> None:
    """Messaging a person is the clearest statement that you know them."""
    router = router_with_contacts
    faiza = online(router, "faiza")
    online(router, "aya")

    router.handle(faiza, Frame(type=MessageType.MSG, to="aya", body="hello"))

    assert router.contacts.of("faiza") == ["aya"]
    assert router.contacts.of("aya") == ["faiza"], "their reply must come from a known name"


def test_adding_somebody_who_does_not_exist_says_so(router_with_contacts) -> None:
    """Silence here is what made adding a room member look like it worked."""
    router = router_with_contacts
    faiza = online(router, "faiza")

    router.handle(faiza, Frame(type=MessageType.ADD_CONTACT, data={"user": "nobody"}))

    assert faiza.last().data["code"] == "NO_SUCH_USER"
    assert router.contacts.of("faiza") == []


def test_you_cannot_add_yourself(router_with_contacts) -> None:
    router = router_with_contacts
    faiza = online(router, "faiza")

    router.handle(faiza, Frame(type=MessageType.ADD_CONTACT, data={"user": "faiza"}))

    assert faiza.last().data["code"] == "BAD_CONTACT"


def test_a_room_is_not_a_contact(router_with_contacts) -> None:
    router = router_with_contacts
    faiza = online(router, "faiza")

    router.handle(faiza, Frame(type=MessageType.ADD_CONTACT, data={"user": "#study"}))

    assert faiza.last().data["code"] == "BAD_CONTACT"


def test_a_contact_can_be_removed(router_with_contacts) -> None:
    router = router_with_contacts
    faiza = online(router, "faiza")
    online(router, "aya")
    router.handle(faiza, Frame(type=MessageType.ADD_CONTACT, data={"user": "aya"}))

    router.handle(faiza, Frame(type=MessageType.REMOVE_CONTACT, data={"user": "aya"}))

    assert router.contacts.of("faiza") == []


def test_inviting_an_unknown_name_names_it(router_with_contacts) -> None:
    """The dialog used to close with nothing having happened."""
    router = router_with_contacts
    faiza = online(router, "faiza")
    online(router, "aya")
    router.handle(faiza, Frame(type=MessageType.CREATE_ROOM, data={"room": "#study"}))
    quiet(faiza)

    router.handle(
        faiza,
        Frame(type=MessageType.INVITE, data={"room": "#study", "members": ["aya", "ghost"]}),
    )

    assert router.rooms.members("#study") == {"faiza", "aya"}
    errors = [f for f in faiza.outbox if f.type is MessageType.ERROR]
    assert errors and "ghost" in errors[-1].data["message"]
