"""ChatController tests. Phase 3.

The controller only ever calls methods on the connection it was given, so a
fake that records frames is a complete stand-in. No sockets here either.
"""

from __future__ import annotations

import pytest

from im.client.controller.chat import ChatController
from im.client.model.chat import ChatModel
from im.client.model.events import ErrorRaised
from im.common.frames import Frame, MessageType


class FakeConnection:
    """Records what the controller asked it to send."""

    def __init__(self) -> None:
        self.sent: list[Frame] = []

    def message(self, to: str, body: str, nonce: str | None = None) -> Frame:
        frame = Frame(type=MessageType.MSG, to=to, body=body, nonce=nonce)
        self.sent.append(frame)
        return frame

    def ping(self) -> None:
        self.sent.append(Frame(type=MessageType.PING))

    def typing(self, to: str, on: bool = True) -> None:
        self.sent.append(Frame(type=MessageType.TYPING, to=to, data={"on": on}))

    def create_room(self, room: str, members: list[str] | None = None) -> None:
        data: dict = {"room": room}
        if members:
            data["members"] = list(members)
        self.sent.append(Frame(type=MessageType.CREATE_ROOM, data=data))

    def add_contact(self, user: str) -> None:
        self.sent.append(Frame(type=MessageType.ADD_CONTACT, data={"user": user}))

    def remove_contact(self, user: str) -> None:
        self.sent.append(Frame(type=MessageType.REMOVE_CONTACT, data={"user": user}))

    def invite(self, room: str, members: list[str]) -> None:
        self.sent.append(
            Frame(type=MessageType.INVITE, data={"room": room, "members": list(members)})
        )

    def join(self, room: str) -> None:
        self.sent.append(Frame(type=MessageType.JOIN, data={"room": room}))

    def leave(self, room: str) -> None:
        self.sent.append(Frame(type=MessageType.LEAVE, data={"room": room}))


@pytest.fixture
def parts() -> tuple[FakeConnection, ChatModel, ChatController]:
    connection = FakeConnection()
    model = ChatModel()
    return connection, model, ChatController(connection, model)


# ------------------------------------------------------- gestures -> frames ---


def test_a_message_goes_to_the_conversation_on_screen(parts) -> None:
    connection, model, controller = parts
    model.set_identity("alice")
    model.select("bob")

    controller.send("hello 你好 🔐")

    assert len(connection.sent) == 1
    assert connection.sent[0].to == "bob"
    assert connection.sent[0].body == "hello 你好 🔐"


def test_your_own_message_appears_immediately(parts) -> None:
    """Waiting for the ACK would make your own messages appear a round trip
    late, which reads as lag."""
    _, model, controller = parts
    model.set_identity("alice")
    model.select("bob")

    controller.send("hello")

    message = model.conversation("bob").last()
    assert message is not None
    assert message.mine
    assert message.sender == "alice"
    assert message.body == "hello"


def test_a_message_with_nobody_selected_is_not_sent(parts) -> None:
    connection, _, controller = parts
    assert controller.send("into the void") is None
    assert connection.sent == []


def test_whitespace_is_not_sent(parts) -> None:
    connection, model, controller = parts
    model.select("bob")
    assert controller.send("   ") is None
    assert connection.sent == []


# ------------------------------------------------------- frames -> the model ---


def test_a_direct_message_is_filed_under_its_sender(parts) -> None:
    """Not under `to`, which is us."""
    _, model, controller = parts
    model.set_identity("alice")

    controller.on_frame(Frame(type=MessageType.MSG, sender="bob", to="alice", body="hi"))

    assert "bob" in model.conversations
    assert "alice" not in model.conversations
    assert model.conversation("bob").last().body == "hi"


def test_a_room_message_is_filed_under_the_room(parts) -> None:
    _, model, controller = parts
    model.set_identity("alice")

    controller.on_frame(Frame(type=MessageType.MSG, sender="bob", to="#general", body="hello room"))

    assert model.conversation("#general").last().sender == "bob"
    assert "bob" not in model.conversations


def test_an_incoming_message_is_not_marked_as_yours(parts) -> None:
    _, model, controller = parts
    controller.on_frame(Frame(type=MessageType.MSG, sender="bob", to="alice", body="hi"))
    assert model.conversation("bob").last().mine is False


def test_presence_updates_the_roster(parts) -> None:
    _, model, controller = parts

    controller.on_frame(Frame(type=MessageType.PRESENCE, data={"user": "bob", "state": "ONLINE"}))
    assert model.is_online("bob")

    controller.on_frame(Frame(type=MessageType.PRESENCE, data={"user": "bob", "state": "OFFLINE"}))
    assert not model.is_online("bob")


def test_login_ok_establishes_identity_and_roster(parts) -> None:
    _, model, controller = parts

    controller.on_frame(
        Frame(
            type=MessageType.LOGIN_OK,
            data={"user": "alice", "roster": ["bob", "carol"], "rooms": []},
        )
    )

    assert model.username == "alice"
    assert model.online_users() == ["bob", "carol"]


def test_an_error_frame_is_surfaced(parts) -> None:
    _, model, controller = parts
    seen: list = []
    model.subscribe(seen.append)

    controller.on_frame(
        Frame(
            type=MessageType.ERROR,
            data={"code": "USER_OFFLINE", "message": "bob is not online"},
        )
    )

    assert ErrorRaised("USER_OFFLINE", "bob is not online") in seen


@pytest.mark.parametrize("kind", [MessageType.ACK, MessageType.PONG, MessageType.OK])
def test_bookkeeping_frames_change_nothing(parts, kind: MessageType) -> None:
    _, model, controller = parts
    seen: list = []
    model.subscribe(seen.append)

    controller.on_frame(Frame(type=kind, data={"ref": "abc"}))

    assert seen == []


def test_a_message_with_nobody_to_attribute_it_to_is_dropped(parts) -> None:
    _, model, controller = parts
    controller.on_frame(Frame(type=MessageType.MSG, body="from nowhere"))
    assert model.conversations == {}


def test_connection_state_reaches_the_model(parts) -> None:
    _, model, controller = parts
    controller.on_state("ONLINE")
    assert model.connection_state == "ONLINE"


# ------------------------------------------------------------------ typing ---


def test_typing_goes_to_the_conversation_on_screen(parts) -> None:
    connection, model, controller = parts
    model.select("bob")

    controller.typing(True)

    assert connection.sent[0].type is MessageType.TYPING
    assert connection.sent[0].to == "bob"
    assert connection.sent[0].data == {"on": True}


def test_a_typing_hint_with_nobody_selected_is_not_sent(parts) -> None:
    connection, _, controller = parts
    controller.typing(True)
    assert connection.sent == []


def test_a_direct_typing_hint_is_filed_under_its_sender(parts) -> None:
    _, model, controller = parts

    controller.on_frame(Frame(type=MessageType.TYPING, sender="bob", to="alice", data={"on": True}))

    assert model.typing_in("bob") == ("bob",)


def test_a_room_typing_hint_is_filed_under_the_room(parts) -> None:
    _, model, controller = parts

    controller.on_frame(
        Frame(type=MessageType.TYPING, sender="bob", to="#general", data={"on": True})
    )

    assert model.typing_in("#general") == ("bob",)
    assert model.typing_in("bob") == ()


# ------------------------------------------------------------------- rooms ---


def test_room_commands_reach_the_connection(parts) -> None:
    connection, _, controller = parts

    controller.create_room("#general")
    controller.join("#random")
    controller.leave("#general")

    assert [(f.type, f.data["room"]) for f in connection.sent] == [
        (MessageType.CREATE_ROOM, "#general"),
        (MessageType.JOIN, "#random"),
        (MessageType.LEAVE, "#general"),
    ]


def test_room_state_records_the_membership(parts) -> None:
    _, model, controller = parts

    controller.on_frame(
        Frame(
            type=MessageType.ROOM_STATE,
            to="#general",
            data={"room": "#general", "members": ["bob", "alice"]},
        )
    )

    assert model.room_members("#general") == ("alice", "bob")


def test_a_joined_room_appears_in_the_chat_list(parts) -> None:
    _, model, controller = parts

    controller.on_frame(
        Frame(type=MessageType.ROOM_STATE, data={"room": "#general", "members": ["alice"]})
    )

    assert "#general" in model.conversations


def test_leaving_removes_you_from_your_rooms(parts) -> None:
    """The model derives membership from the server's list rather than keeping
    a second copy that could fall out of step."""
    _, model, controller = parts
    model.set_identity("alice")

    controller.on_frame(
        Frame(type=MessageType.ROOM_STATE, data={"room": "#general", "members": ["alice", "bob"]})
    )
    assert model.my_rooms() == ["#general"]

    controller.on_frame(
        Frame(type=MessageType.ROOM_STATE, data={"room": "#general", "members": ["bob"]})
    )
    assert model.my_rooms() == []


def test_a_room_can_be_created_with_members(parts) -> None:
    connection, _, controller = parts

    controller.create_room("#study", ["aya", "keisha"])

    assert connection.sent[0].data == {"room": "#study", "members": ["aya", "keisha"]}


def test_creating_a_room_with_nobody_sends_no_member_list(parts) -> None:
    """An empty list would be noise on the wire, and the server ignores it."""
    connection, _, controller = parts

    controller.create_room("#study", [])

    assert "members" not in connection.sent[0].data


def test_inviting_reaches_the_connection(parts) -> None:
    connection, _, controller = parts

    controller.invite("#study", ["faiza"])

    assert connection.sent[0].type is MessageType.INVITE
    assert connection.sent[0].data["members"] == ["faiza"]


# ------------------------------------------------------- history, decrypted ---


def encrypted_pair():
    """Two controllers that really can encrypt to each other."""
    from im.crypto.identity import Identity
    from im.crypto.keyring import Keyring

    alice, bob = Identity.generate(), Identity.generate()
    a_ring, b_ring = Keyring(alice, me="alice"), Keyring(bob, me="bob")
    a_ring.remember("bob", bob.public_b64)
    b_ring.remember("alice", alice.public_b64)

    a_model, b_model = ChatModel(), ChatModel()
    a_model.set_identity("alice")
    b_model.set_identity("bob")
    a = ChatController(FakeConnection(), a_model, keyring=a_ring)
    b = ChatController(FakeConnection(), b_model, keyring=b_ring)
    return a, b


def history_frame(conversation: str, rows: list[dict]) -> Frame:
    return Frame(
        type=MessageType.HISTORY_RESULT,
        to=conversation,
        data={"room": conversation, "messages": rows},
    )


def test_stored_messages_are_decrypted_on_the_way_in() -> None:
    """Until this worked, scrollback rendered as base64. Nothing in the
    history path decrypted anything."""
    alice, bob = encrypted_pair()
    ciphertext, nonce = alice.keyring.seal("bob", "friday at six", "alice")

    bob.on_frame(
        history_frame("alice", [{"id": "1", "from": "alice", "body": ciphertext, "n": nonce,
                                 "ts": 1}])
    )

    assert [m.body for m in bob.model.conversation("alice").messages] == ["friday at six"]


def test_your_own_stored_messages_are_decrypted_too() -> None:
    """A message we sent was sealed under the key we share with the recipient,
    and X25519 is symmetric, so the same key opens it."""
    alice, _bob = encrypted_pair()
    ciphertext, nonce = alice.keyring.seal("bob", "friday at six", "alice")

    alice.on_frame(
        history_frame("bob", [{"id": "1", "from": "alice", "body": ciphertext, "n": nonce,
                               "ts": 1}])
    )

    assert [m.body for m in alice.model.conversation("bob").messages] == ["friday at six"]


def test_your_own_stored_room_messages_are_decrypted() -> None:
    """The one case where the peer is yourself: a room message is sealed to
    every member including the sender, precisely so this works."""
    alice, _bob = encrypted_pair()
    envelopes = alice.keyring.seal_for_members(["bob"], "friday at six", "alice")
    ciphertext, nonce = envelopes["alice"]

    alice.on_frame(
        history_frame("#study", [{"id": "1", "from": "alice", "body": ciphertext, "n": nonce,
                                  "ts": 1}])
    )

    assert [m.body for m in alice.model.conversation("#study").messages] == ["friday at six"]


def test_a_stored_message_that_will_not_open_is_reported_in_place() -> None:
    """Not dropped. A message the user cannot read is something they need to
    know about, not something to hide."""
    _alice, bob = encrypted_pair()

    bob.on_frame(
        history_frame("alice", [{"id": "1", "from": "alice", "body": "bm90IHJlYWw=",
                                 "n": "bm90IHJlYWw=", "ts": 1}])
    )

    assert "could not decrypt" in bob.model.conversation("alice").messages[0].body


def test_plaintext_history_still_loads_without_a_keyring() -> None:
    connection = FakeConnection()
    model = ChatModel()
    model.set_identity("alice")
    controller = ChatController(connection, model)

    controller.on_frame(
        history_frame("bob", [{"id": "1", "from": "bob", "body": "hello", "ts": 1}])
    )

    assert [m.body for m in model.conversation("bob").messages] == ["hello"]


def test_login_ok_loads_the_contact_list(parts) -> None:
    """Without this the list was rebuilt from presence every start."""
    _connection, model, controller = parts

    controller.on_frame(
        Frame(
            type=MessageType.LOGIN_OK,
            data={
                "user": "turjo",
                "roster": ["aya"],
                "contacts": [{"user": "faiza", "online": False}],
            },
        )
    )

    assert "faiza" in model.contacts
    assert model.known_users() == ["aya", "faiza"]


def test_a_contacts_frame_replaces_the_list(parts) -> None:
    _connection, model, controller = parts

    controller.on_frame(
        Frame(type=MessageType.CONTACTS, data={"contacts": [{"user": "aya", "online": True}]})
    )

    assert sorted(model.contacts) == ["aya"]


def test_adding_a_contact_reaches_the_connection(parts) -> None:
    connection, _model, controller = parts

    controller.add_contact("faiza")

    assert connection.sent[-1].type is MessageType.ADD_CONTACT
    assert connection.sent[-1].data["user"] == "faiza"
