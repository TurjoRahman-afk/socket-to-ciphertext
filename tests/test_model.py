"""ChatModel and Conversation tests. Phase 3.

The phase 3 exit criteria says the model must have unit tests that never open
a socket. Nothing in this file imports im.client.net or im.server.
"""

from __future__ import annotations

import pytest

from im.client.model.chat import ChatModel
from im.client.model.conversation import Conversation, Message
from im.client.model.events import (
    ConnectionStateChanged,
    ConversationSelected,
    ErrorRaised,
    IdentityEstablished,
    MessageAdded,
    PresenceChanged,
    RosterReplaced,
    UnreadChanged,
)


def msg(body: str, sender: str = "bob", mine: bool = False, id: str = "m1") -> Message:
    return Message(id=id, sender=sender, body=body, ts=1_700_000_000_000, mine=mine)


@pytest.fixture
def model() -> ChatModel:
    return ChatModel()


@pytest.fixture
def seen(model: ChatModel) -> list:
    events: list = []
    model.subscribe(events.append)
    return events


# ------------------------------------------------------------- conversation ---


def test_a_conversation_starts_empty() -> None:
    conversation = Conversation("bob")
    assert len(conversation) == 0
    assert conversation.unread == 0
    assert conversation.last() is None


def test_a_room_is_recognised_by_its_name() -> None:
    assert Conversation("#general").is_room
    assert not Conversation("bob").is_room


def test_a_message_to_a_chat_that_is_not_on_screen_is_unread() -> None:
    conversation = Conversation("bob")
    conversation.add(msg("hi"), active=False)
    assert conversation.unread == 1


def test_a_message_to_the_chat_on_screen_is_already_read() -> None:
    conversation = Conversation("bob")
    conversation.add(msg("hi"), active=True)
    assert conversation.unread == 0


def test_your_own_messages_are_never_unread() -> None:
    """You have just typed it -- counting it would be absurd."""
    conversation = Conversation("bob")
    conversation.add(msg("hi", sender="me", mine=True), active=False)
    assert conversation.unread == 0


def test_marking_read_reports_whether_it_changed() -> None:
    conversation = Conversation("bob")
    assert conversation.mark_read() is False  # nothing to clear
    conversation.add(msg("hi"), active=False)
    assert conversation.mark_read() is True
    assert conversation.unread == 0


# -------------------------------------------------------------------- model ---


def test_identity_is_announced(model: ChatModel, seen: list) -> None:
    model.set_identity("alice")
    assert model.username == "alice"
    assert seen == [IdentityEstablished("alice")]


def test_connection_state_changes_are_announced(model: ChatModel, seen: list) -> None:
    model.set_connection_state("ONLINE")
    assert seen == [ConnectionStateChanged("ONLINE")]


def test_an_unchanged_connection_state_is_not_announced(model: ChatModel, seen: list) -> None:
    model.set_connection_state("DISCONNECTED")  # already the initial value
    assert seen == []


def test_the_roster_marks_absent_contacts_offline_rather_than_forgetting_them(
    model: ChatModel,
) -> None:
    """You may still have their conversation open."""
    model.replace_roster(["bob", "carol"])
    model.replace_roster(["carol"])

    assert model.is_online("carol")
    assert not model.is_online("bob")
    assert "bob" in model.roster


def test_presence_changes_are_announced(model: ChatModel, seen: list) -> None:
    model.set_presence("bob", True)
    assert seen == [PresenceChanged("bob", True)]


def test_repeated_presence_is_not_announced_twice(model: ChatModel, seen: list) -> None:
    model.set_presence("bob", True)
    seen.clear()
    model.set_presence("bob", True)
    assert seen == []


def test_online_users_lists_only_the_online_ones(model: ChatModel) -> None:
    model.replace_roster(["bob", "carol"])
    model.set_presence("bob", False)
    assert model.online_users() == ["carol"]


def test_a_conversation_is_created_on_first_use(model: ChatModel) -> None:
    """A message can arrive from someone you have never spoken to."""
    assert model.conversations == {}
    conversation = model.conversation("stranger")
    assert conversation is model.conversation("stranger")  # not recreated


def test_adding_a_message_announces_it(model: ChatModel, seen: list) -> None:
    model.add_message("bob", msg("hello"))
    assert seen[0] == MessageAdded("bob", msg("hello"))


def test_a_message_to_another_chat_raises_the_unread_count(model: ChatModel, seen: list) -> None:
    model.select("carol")
    seen.clear()

    model.add_message("bob", msg("hello"))

    assert model.conversation("bob").unread == 1
    assert UnreadChanged("bob", 1) in seen


def test_a_message_to_the_open_chat_does_not(model: ChatModel, seen: list) -> None:
    model.select("bob")
    seen.clear()

    model.add_message("bob", msg("hello"))

    assert model.conversation("bob").unread == 0
    assert not any(isinstance(e, UnreadChanged) for e in seen)


def test_selecting_a_conversation_marks_it_read(model: ChatModel, seen: list) -> None:
    model.add_message("bob", msg("hello"))
    seen.clear()

    model.select("bob")

    assert model.conversation("bob").unread == 0
    assert ConversationSelected("bob") in seen
    assert UnreadChanged("bob", 0) in seen


def test_unread_total_adds_up_across_conversations(model: ChatModel) -> None:
    """The slide's 'multiple conversations at the same time' requirement."""
    model.add_message("bob", msg("one"))
    model.add_message("carol", msg("two", sender="carol"))
    model.add_message("#general", msg("three", sender="dave"))

    assert model.unread_total() == 3
    model.select("bob")
    assert model.unread_total() == 2


def test_conversations_are_listed_with_rooms_first(model: ChatModel) -> None:
    for key in ("zoe", "bob", "#general", "#random"):
        model.conversation(key)
    assert model.keys() == ["#general", "#random", "bob", "zoe"]


def test_errors_are_announced_but_not_stored(model: ChatModel, seen: list) -> None:
    model.raise_error("USER_OFFLINE", "bob is not online")
    assert seen == [ErrorRaised("USER_OFFLINE", "bob is not online")]


# ---------------------------------------------------------------- observers ---


def test_two_views_can_watch_one_model(model: ChatModel) -> None:
    """The whole MVC claim in one test: console and Tk, side by side."""
    console: list = []
    tk: list = []
    model.subscribe(console.append)
    model.subscribe(tk.append)

    model.add_message("bob", msg("hello"))

    assert console == tk
    assert len(console) == 2  # MessageAdded, UnreadChanged


def test_unsubscribing_stops_the_events(model: ChatModel) -> None:
    seen: list = []
    stop = model.subscribe(seen.append)
    stop()

    model.set_identity("alice")

    assert seen == []


def test_a_listener_may_unsubscribe_while_handling_an_event(model: ChatModel) -> None:
    """Mutating the listener list mid-iteration would skip the next one."""
    seen: list = []

    def once(event) -> None:
        seen.append(event)
        stop()

    stop = model.subscribe(once)
    other: list = []
    model.subscribe(other.append)

    model.set_identity("alice")
    model.set_identity("bob")

    assert len(seen) == 1
    assert len(other) == 2


def test_roster_replacement_reports_everyone_known(model: ChatModel, seen: list) -> None:
    model.replace_roster(["carol", "bob"])
    assert RosterReplaced(("bob", "carol")) in seen
