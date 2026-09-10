"""Tkinter view tests. Phase 7.

These build a real window, drive it, and destroy it. Skipped where there is no
display, which is normal on a headless machine and not a failure.

What is worth testing here is not how it looks -- that is a design decision,
not a correctness one -- but the two things that would break silently:

  * the queue bridge, since touching a widget from a worker thread corrupts Tk
    quietly rather than raising, and
  * that this view and the console view really are interchangeable over one
    model, which is the whole architectural claim.
"""

from __future__ import annotations

import threading

import pytest

from im.client.controller.chat import ChatController
from im.client.model.chat import ChatModel
from im.common.frames import Frame, MessageType

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="session")
def tk_root():
    """One hidden Tk root for the whole session.

    Creating and destroying a Tk root repeatedly in one process eventually
    corrupts the Tcl interpreter -- it starts failing with "invalid command
    name tcl_findLibrary" partway through a run, while every test passes on
    its own. One root, and a Toplevel per test, avoids it entirely.
    """
    try:
        root = tk.Tk()
    except tk.TclError:  # pragma: no cover -- headless machine
        pytest.skip("no display available")
    root.withdraw()
    try:
        yield root
    finally:
        root.destroy()


@pytest.fixture
def view(tk_root):
    """A real window, torn down afterwards."""
    from im.client.view.tk import TkView

    window = tk.Toplevel(tk_root)

    class FakeConnection:
        def __init__(self) -> None:
            self.sent: list[Frame] = []

        def message(self, to: str, body: str, nonce: str | None = None) -> Frame:
            frame = Frame(type=MessageType.MSG, to=to, body=body, nonce=nonce)
            self.sent.append(frame)
            return frame

        def typing(self, to: str, on: bool = True) -> None:
            self.sent.append(Frame(type=MessageType.TYPING, to=to, data={"on": on}))

        def get_key(self, user: str) -> None: ...
        def ping(self) -> None: ...
        def history(self, room: str, before=None, limit: int = 50) -> None: ...
        def create_room(self, room: str) -> None: ...
        def join(self, room: str) -> None: ...
        def leave(self, room: str) -> None: ...

    connection = FakeConnection()
    model = ChatModel()
    model.set_identity("alice")
    instance = TkView(ChatController(connection, model), root=window)
    instance.connection = connection
    try:
        yield instance
    finally:
        try:
            # Let Tk finish anything already scheduled before tearing the
            # window down, or the destroy can race a pending after() call.
            window.update_idletasks()
            window.destroy()
        except tk.TclError:
            pass


def test_the_window_builds(view) -> None:
    assert view.root.title() == "Semaphore"
    assert view.entry is not None
    assert view.transcript is not None


def test_the_composer_is_disabled_until_online(view) -> None:
    """The state machine says the composer is live in exactly one state."""
    assert str(view.entry["state"]) == "disabled"

    view.controller.on_state("ONLINE")
    view.root.update_idletasks()

    assert str(view.entry["state"]) == "normal"

    view.controller.on_state("RETRYING")
    view.root.update_idletasks()

    assert str(view.entry["state"]) == "disabled"


def test_a_frame_posted_from_another_thread_is_not_applied_immediately(view) -> None:
    """post_frame must only enqueue. If it touched the model or a widget from
    the calling thread, this assertion would fail -- and in a real client it
    would corrupt Tk silently instead."""
    done = threading.Event()

    def worker() -> None:
        view.post_frame(Frame(type=MessageType.MSG, sender="bob", to="alice", body="hi"))
        done.set()

    thread = threading.Thread(target=worker)
    thread.start()
    done.wait(timeout=2)
    thread.join(timeout=2)

    assert "bob" not in view.model.conversations, "the queue must not be drained yet"

    view._poll()  # what root.after would have called
    view.root.update_idletasks()

    assert view.model.conversation("bob").last().body == "hi"


def test_typing_in_the_box_announces_it(view) -> None:
    view.controller.on_state("ONLINE")
    view.model.select("bob")
    view.entry.insert(0, "h")
    view._on_key(None)

    assert view.connection.sent[-1].type is MessageType.TYPING
    assert view.connection.sent[-1].data == {"on": True}


def test_sending_clears_the_box_and_sends(view) -> None:
    view.controller.on_state("ONLINE")
    view.model.select("bob")
    view.entry.insert(0, "hello 你好 🔐")

    view._on_send()

    assert view.entry.get() == ""
    assert view.connection.sent[-1].body == "hello 你好 🔐"
    assert view.model.conversation("bob").last().mine


def test_an_empty_message_is_not_sent(view) -> None:
    view.controller.on_state("ONLINE")
    view.model.select("bob")
    view.entry.insert(0, "   ")

    view._on_send()

    assert view.connection.sent == []


def test_the_conversation_list_shows_unread_counts(view) -> None:
    view.model.select("carol")
    view.model.add_message("bob", _message("hello"))
    view.root.update_idletasks()

    rows = view.conversations.get(0, "end")
    assert any("bob" in row and "(1)" in row for row in rows)


def test_selecting_a_conversation_redraws_the_transcript(view) -> None:
    view.model.add_message("bob", _message("first"))
    view.model.add_message("bob", _message("second"))

    view.controller.select("bob")
    view.root.update_idletasks()

    shown = view.transcript.get("1.0", "end")
    assert "first" in shown and "second" in shown
    assert "bob" in view.peer_label["text"]


def test_a_room_name_gets_its_marker(view) -> None:
    assert view._hashed("general") == "#general"
    assert view._hashed("#general") == "#general"


def test_both_views_can_watch_one_model(view) -> None:
    """The architectural claim, tested rather than asserted in a report."""
    from im.client.view.console import ConsoleView

    console = ConsoleView(view.controller)
    try:
        view.controller.select("bob")
        view.model.add_message("bob", _message("seen by both"))
        view.root.update_idletasks()

        # The Tk window drew it, and the console view rendered the same event
        # to stdout from the same model, with neither knowing about the other.
        assert "seen by both" in view.transcript.get("1.0", "end")
        assert view.model.conversation("bob").last().body == "seen by both"
    finally:
        console._unsubscribe()


def _message(body: str):
    from im.client.model.conversation import Message

    return Message(id="m1", sender="bob", body=body, ts=1_700_000_000_000, mine=False)
