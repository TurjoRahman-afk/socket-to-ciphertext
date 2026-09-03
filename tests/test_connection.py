"""ServerConnection tests. Phase 3.

These run against a real ChatServer on a real socket, because what is being
checked is precisely the part the state machine tests cannot reach: that the
threads, the queue and the handshake behave when there is a peer at the other
end. Port 0 so the OS picks a free one and the suite never collides with a
server the user is running.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from im.client.net.connection import NotConnected, ServerConnection
from im.client.net.state import ConnectionState
from im.common.frames import Frame, MessageType
from im.server.server import ChatServer

HASH = "pretend-digest"


@pytest.fixture
def server() -> Iterator[ChatServer]:
    server = ChatServer("127.0.0.1", 0)
    server.bind()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)


def connect(server: ChatServer, **kwargs) -> ServerConnection:
    host, port = server.address
    conn = ServerConnection(host, port, **kwargs)
    conn.connect()
    return conn


def signed_in(server: ChatServer, user: str, **kwargs) -> ServerConnection:
    conn = connect(server, **kwargs)
    conn.register(user, HASH)
    conn.login(user, HASH)
    return conn


def wait_for(inbox: list[Frame], kind: MessageType, timeout: float = 2.0) -> Frame:
    """Wait for a frame of one type. Frames arrive on the reader thread, so a
    test has to wait for them rather than assume they have landed."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for frame in list(inbox):
            if frame.type is kind:
                return frame
        time.sleep(0.02)
    raise AssertionError(f"no {kind} within {timeout}s; got {[f.type for f in inbox]}")


# --------------------------------------------------------------- handshake ---


def test_connecting_leaves_us_authenticating(server: ChatServer) -> None:
    with connect(server) as conn:
        assert conn.state is ConnectionState.AUTHENTICATING
        assert not conn.can_send


def test_connecting_to_nothing_fails_and_retries(server: ChatServer) -> None:
    host, port = server.address
    server.shutdown()  # nothing is listening now
    conn = ServerConnection(host, port)

    with pytest.raises(ConnectionError, match="could not reach"):
        conn.connect(timeout=1)

    assert conn.state is ConnectionState.RETRYING


def test_register_then_login_reaches_online(server: ChatServer) -> None:
    with connect(server) as conn:
        assert conn.register("alice", HASH).type is MessageType.OK

        reply = conn.login("alice", HASH)

        assert reply.type is MessageType.LOGIN_OK
        assert conn.state is ConnectionState.ONLINE
        assert conn.can_send
        assert conn.username == "alice"


def test_login_ok_carries_the_roster(server: ChatServer) -> None:
    with signed_in(server, "alice"), connect(server) as bob:
        bob.register("bob", HASH)
        reply = bob.login("bob", HASH)

        assert reply.data["roster"] == ["alice"]
        assert reply.data["rooms"] == []


def test_a_wrong_password_leaves_us_disconnected(server: ChatServer) -> None:
    """A refusal is not a network problem, so it waits for the user."""
    with connect(server) as conn:
        conn.register("alice", HASH)

        reply = conn.login("alice", "wrong-digest")

        assert reply.type is MessageType.ERROR
        assert reply.data["code"] == "BAD_CREDENTIALS"
        assert conn.state is ConnectionState.DISCONNECTED
        assert conn.username is None


def test_you_cannot_log_in_twice_on_one_connection(server: ChatServer) -> None:
    with signed_in(server, "alice") as conn:
        with pytest.raises(NotConnected, match="cannot login while ONLINE"):
            conn.login("alice", HASH)


# --------------------------------------------------------------- messaging ---


def test_a_message_reaches_the_other_client(server: ChatServer) -> None:
    inbox: list[Frame] = []
    with signed_in(server, "alice") as alice, signed_in(server, "bob", on_frame=inbox.append) as _:
        alice.message("bob", "hello 你好 🔐")

        delivered = wait_for(inbox, MessageType.MSG)
        assert delivered.sender == "alice"
        assert delivered.body == "hello 你好 🔐"


def test_the_sender_gets_an_ack(server: ChatServer) -> None:
    acks: list[Frame] = []
    with signed_in(server, "alice", on_frame=acks.append) as alice, signed_in(server, "bob"):
        sent = alice.message("bob", "hi")

        assert wait_for(acks, MessageType.ACK).data["ref"] == sent.id


def test_messaging_someone_offline_reports_an_error(server: ChatServer) -> None:
    errors: list[Frame] = []
    with signed_in(server, "alice", on_frame=errors.append) as alice:
        alice.message("nobody", "anyone there")

        assert wait_for(errors, MessageType.ERROR).data["code"] == "USER_OFFLINE"


def test_sending_before_login_is_refused_locally(server: ChatServer) -> None:
    """Caught client-side, so a stray send never reaches the wire."""
    with connect(server) as conn:
        with pytest.raises(NotConnected, match="cannot send while AUTHENTICATING"):
            conn.message("bob", "too early")


def test_ping_is_answered(server: ChatServer) -> None:
    inbox: list[Frame] = []
    with signed_in(server, "alice", on_frame=inbox.append) as conn:
        conn.ping()
        assert wait_for(inbox, MessageType.PONG)


# ---------------------------------------------------------------- presence ---


def test_presence_arrives_when_someone_logs_in(server: ChatServer) -> None:
    inbox: list[Frame] = []
    with signed_in(server, "alice", on_frame=inbox.append):
        with signed_in(server, "bob"):
            announcement = wait_for(inbox, MessageType.PRESENCE)
            assert announcement.data == {"user": "bob", "state": "ONLINE"}


def test_presence_arrives_when_someone_leaves(server: ChatServer) -> None:
    inbox: list[Frame] = []
    with signed_in(server, "alice", on_frame=inbox.append):
        bob = signed_in(server, "bob")
        wait_for(inbox, MessageType.PRESENCE)
        inbox.clear()

        bob.close()

        assert wait_for(inbox, MessageType.PRESENCE).data == {"user": "bob", "state": "OFFLINE"}


# --------------------------------------------------------------- lifecycle ---


def test_the_state_observer_sees_the_whole_handshake(server: ChatServer) -> None:
    seen: list[ConnectionState] = []
    with connect(server, on_state=seen.append) as conn:
        conn.register("alice", HASH)
        conn.login("alice", HASH)

    assert seen == [
        ConnectionState.CONNECTING,
        ConnectionState.AUTHENTICATING,
        ConnectionState.ONLINE,
        ConnectionState.CLOSED,
    ]


def test_closing_is_idempotent(server: ChatServer) -> None:
    conn = signed_in(server, "alice")
    conn.close()
    conn.close()
    assert conn.state is ConnectionState.CLOSED


def test_sending_after_close_is_refused(server: ChatServer) -> None:
    conn = signed_in(server, "alice")
    conn.close()
    with pytest.raises(NotConnected, match="cannot send while CLOSED"):
        conn.message("bob", "too late")


def test_the_server_going_away_moves_us_to_retrying(server: ChatServer) -> None:
    conn = signed_in(server, "alice")
    try:
        server.shutdown()

        deadline = time.monotonic() + 2
        while conn.state is ConnectionState.ONLINE and time.monotonic() < deadline:
            time.sleep(0.02)

        assert conn.state is ConnectionState.RETRYING
    finally:
        conn.close()


def test_a_listener_that_raises_does_not_kill_the_connection(server: ChatServer) -> None:
    """A broken view must not take the socket down with it."""

    def explode(frame: Frame) -> None:
        raise RuntimeError("the view is on fire")

    with signed_in(server, "alice", on_frame=explode) as alice, signed_in(server, "bob"):
        alice.message("bob", "one")
        time.sleep(0.3)

        assert alice.state is ConnectionState.ONLINE
        alice.message("bob", "two")  # still usable
