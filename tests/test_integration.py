"""Phase 8: the whole system, and how it behaves under strain.

Everything here runs real servers on real sockets with real threads. The unit
tests establish that each part is right; these establish that the parts still
work when there are several of them running at once.
"""

from __future__ import annotations

import inspect
import threading
import time
from collections.abc import Iterator

import pytest

from im.client.net.backoff import Backoff
from im.client.net.connection import ServerConnection
from im.client.net.session import Session
from im.client.net.state import ConnectionState
from im.common.frames import Frame, MessageType
from im.server.server import ChatServer

HASH = "pretend-digest"


@pytest.fixture
def server() -> Iterator[ChatServer]:
    instance = ChatServer("127.0.0.1", 0)
    instance.bind()
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        thread.join(timeout=3)


def join(
    server: ChatServer,
    user: str,
    inbox: list[Frame] | None = None,
    **kwargs,
) -> ServerConnection:
    host, port = server.address
    conn = ServerConnection(
        host, port, on_frame=(inbox.append if inbox is not None else None), **kwargs
    )
    conn.connect()
    conn.register(user, HASH)
    conn.login(user, HASH)
    return conn


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def bodies(inbox: list[Frame]) -> list[str]:
    return [f.body or "" for f in inbox if f.type is MessageType.MSG]


# ----------------------------------------------------------------- backoff ---


def test_backoff_grows_and_then_stops_growing() -> None:
    """Exponential so a downed server is not hammered; capped so a client
    never looks broken to the person waiting on it."""
    backoff = Backoff(first=1, factor=2, maximum=8, jitter=0)

    assert [backoff.next_delay() for _ in range(6)] == [1, 2, 4, 8, 8, 8]


def test_a_successful_connection_resets_the_schedule() -> None:
    """A client up for days must not begin its next reconnect at the cap."""
    backoff = Backoff(first=1, factor=2, maximum=30, jitter=0)
    for _ in range(5):
        backoff.next_delay()

    backoff.reset()

    assert backoff.next_delay() == 1


def test_jitter_spreads_clients_out() -> None:
    """Without it, every client dropped by one event retries in the same
    instant and the server that just came back is knocked over again."""
    delays = {Backoff(first=10, factor=1, maximum=10).next_delay() for _ in range(100)}

    assert len(delays) > 50, "delays should not all be identical"
    assert all(7.5 <= d <= 10 for d in delays)


# --------------------------------------------------------------- heartbeat ---


def test_an_unanswered_heartbeat_is_noticed(server: ChatServer) -> None:
    """A dropped link leaves a socket that looks open and never delivers
    anything again. Only an unanswered PING reveals it."""
    conn = join(server, "alice", heartbeat_seconds=0.05)
    try:
        # Every PONG is thrown away, so the link looks dead from here even
        # though the socket is perfectly healthy -- which is exactly what a
        # suspended machine or a dropped wifi link looks like.
        conn._deliver = lambda frame: None

        assert wait_until(lambda: conn.state is ConnectionState.RETRYING, timeout=5)
    finally:
        conn.close()


def test_a_healthy_connection_stays_online(server: ChatServer) -> None:
    """The other half of the same rule: a working link must not be torn down."""
    conn = join(server, "alice", heartbeat_seconds=0.05)
    try:
        time.sleep(1.0)  # many heartbeats, all answered

        assert conn.state is ConnectionState.ONLINE
    finally:
        conn.close()


# ------------------------------------------------------------- three party ---


def test_three_clients_and_a_room(server: ChatServer) -> None:
    """The system as a whole: accounts, a room, fan-out and presence."""
    alice_in: list[Frame] = []
    bob_in: list[Frame] = []
    carol_in: list[Frame] = []

    alice = join(server, "alice", alice_in)
    bob = join(server, "bob", bob_in)
    carol = join(server, "carol", carol_in)

    try:
        alice.create_room("#general")
        assert wait_until(lambda: server.rooms.members("#general") == {"alice"})
        bob.join("#general")
        carol.join("#general")
        assert wait_until(lambda: len(server.rooms.members("#general")) == 3)

        alice.message("#general", "hello everyone")

        assert wait_until(lambda: "hello everyone" in bodies(bob_in))
        assert wait_until(lambda: "hello everyone" in bodies(carol_in))
        assert "hello everyone" not in bodies(alice_in), "the sender already has it"
    finally:
        for conn in (alice, bob, carol):
            conn.close()


def test_messages_arrive_in_the_order_they_were_sent(server: ChatServer) -> None:
    """One TCP stream, one writer thread, one queue -- ordering must hold."""
    inbox: list[Frame] = []
    alice = join(server, "alice")
    bob = join(server, "bob", inbox)

    try:
        for i in range(50):
            alice.message("bob", f"message {i:02d}")

        assert wait_until(lambda: len(bodies(inbox)) == 50, timeout=10)
        assert bodies(inbox) == [f"message {i:02d}" for i in range(50)]
    finally:
        alice.close()
        bob.close()


def test_a_message_reaches_only_its_recipient(server: ChatServer) -> None:
    bob_in: list[Frame] = []
    carol_in: list[Frame] = []
    alice = join(server, "alice")
    bob = join(server, "bob", bob_in)
    carol = join(server, "carol", carol_in)

    try:
        alice.message("bob", "just for bob")

        assert wait_until(lambda: bodies(bob_in) == ["just for bob"])
        time.sleep(0.3)
        assert bodies(carol_in) == []
    finally:
        for conn in (alice, bob, carol):
            conn.close()


# -------------------------------------------------------------------- load ---


def test_twenty_clients_at_once(server: ChatServer) -> None:
    """Twenty connections, forty threads on the server, one room.

    Not a benchmark. The question is whether the registries and the per-client
    queues hold up when several threads are doing the same thing at once --
    the kind of thing a lock in the wrong place breaks.
    """
    count = 20
    inboxes: list[list[Frame]] = [[] for _ in range(count)]
    clients: list[ServerConnection] = []

    try:
        for i in range(count):
            clients.append(join(server, f"user{i:02d}", inboxes[i]))

        assert len(server.sessions) == count

        clients[0].create_room("#load")
        assert wait_until(lambda: server.rooms.exists("#load"))
        for client in clients[1:]:
            client.join("#load")
        assert wait_until(lambda: len(server.rooms.members("#load")) == count, timeout=10)

        clients[0].message("#load", "one message to nineteen people")

        delivered = wait_until(
            lambda: all(
                "one message to nineteen people" in bodies(inbox) for inbox in inboxes[1:]
            ),
            timeout=15,
        )
        assert delivered, "every member should receive the fan-out"
    finally:
        for client in clients:
            client.close()


def test_everyone_talking_at_once(server: ChatServer) -> None:
    """Ten clients each sending ten messages into one room simultaneously.

    Every message must reach every other member exactly once -- no drops from
    a full queue, no duplicates from a retry, and nothing lost to two threads
    writing at the same moment.
    """
    count = 10
    each = 10
    inboxes: list[list[Frame]] = [[] for _ in range(count)]
    clients = [join(server, f"talker{i:02d}", inboxes[i]) for i in range(count)]

    try:
        clients[0].create_room("#busy")
        assert wait_until(lambda: server.rooms.exists("#busy"))
        for client in clients[1:]:
            client.join("#busy")
        assert wait_until(lambda: len(server.rooms.members("#busy")) == count, timeout=10)

        def blast(index: int) -> None:
            for n in range(each):
                clients[index].message("#busy", f"{index}-{n}")

        threads = [threading.Thread(target=blast, args=(i,)) for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        expected = (count - 1) * each  # everyone else's messages, not your own
        assert wait_until(
            lambda: all(len(bodies(inbox)) >= expected for inbox in inboxes), timeout=20
        ), [len(bodies(inbox)) for inbox in inboxes]

        for i, inbox in enumerate(inboxes):
            received = bodies(inbox)
            assert len(received) == len(set(received)), f"client {i} got a duplicate"
            assert not any(text.startswith(f"{i}-") for text in received), (
                f"client {i} received its own message back"
            )
    finally:
        for client in clients:
            client.close()


# --------------------------------------------------------------- reconnect ---


def start_server(port: int = 0, db: str = ":memory:") -> tuple[ChatServer, threading.Thread]:
    instance = ChatServer("127.0.0.1", port, db_path=db)
    instance.bind()
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    return instance, thread


def test_a_session_comes_back_after_the_server_restarts(tmp_path) -> None:
    """The phase 8 exit criteria: pull the network and recover.

    The server is killed outright and started again on the same port and the
    same database. The client is never told to do anything -- the supervisor
    notices, waits out the backoff and logs in again by itself.
    """
    db = str(tmp_path / "im.db")
    server, thread = start_server(db=db)
    host, port = server.address

    session = Session(
        host,
        port,
        "alice",
        HASH,
        heartbeat_seconds=0.1,
        backoff=Backoff(first=0.1, factor=1.5, maximum=1.0, jitter=0),
    )
    assert session.start(register=True).type is MessageType.LOGIN_OK

    try:
        server.shutdown()
        thread.join(timeout=3)
        assert wait_until(lambda: not session.can_send, timeout=5), "the drop must be noticed"

        # Same port, same database, brand new process-worth of state.
        server, thread = start_server(port=port, db=db)

        assert wait_until(lambda: session.can_send, timeout=20), "it should come back by itself"
        assert session.reconnects >= 1
        assert session.state is ConnectionState.ONLINE
    finally:
        session.close()
        server.shutdown()
        thread.join(timeout=3)


def test_a_session_can_send_again_after_reconnecting(tmp_path) -> None:
    db = str(tmp_path / "im.db")
    server, thread = start_server(db=db)
    host, port = server.address

    inbox: list[Frame] = []
    bob = None
    session = Session(
        host, port, "alice", HASH,
        heartbeat_seconds=0.1,
        backoff=Backoff(first=0.1, factor=1.5, maximum=1.0, jitter=0),
    )
    session.start(register=True)

    try:
        server.shutdown()
        thread.join(timeout=3)
        assert wait_until(lambda: not session.can_send, timeout=5)

        server, thread = start_server(port=port, db=db)
        assert wait_until(lambda: session.can_send, timeout=20)

        bob = join(server, "bob", inbox)
        session.message("bob", "still here 你好")

        assert wait_until(lambda: "still here 你好" in bodies(inbox), timeout=10)
    finally:
        if bob is not None:
            bob.close()
        session.close()
        server.shutdown()
        thread.join(timeout=3)


def test_a_session_stops_retrying_when_closed(tmp_path) -> None:
    """Closing must actually stop the supervisor, not leave a thread dialling
    a dead server forever."""
    server, thread = start_server()
    host, port = server.address
    session = Session(
        host, port, "alice", HASH,
        heartbeat_seconds=0.1,
        backoff=Backoff(first=0.1, factor=1.0, maximum=0.1, jitter=0),
    )
    session.start(register=True)

    server.shutdown()
    thread.join(timeout=3)
    session.close()
    time.sleep(0.5)

    assert session._stopped.is_set()
    assert not session._supervisor.is_alive() or session._stopped.is_set()


def test_a_room_created_with_members_reaches_them(server: ChatServer) -> None:
    """The whole feature over a real socket: name a room, name who is in it,
    and the first message lands without anybody having had to JOIN."""
    alice_in: list[Frame] = []
    bob_in: list[Frame] = []
    carol_in: list[Frame] = []

    alice = join(server, "alice", alice_in)
    bob = join(server, "bob", bob_in)
    carol = join(server, "carol", carol_in)

    try:
        alice.create_room("#study", ["bob", "carol"])
        assert wait_until(lambda: server.rooms.members("#study") == {"alice", "bob", "carol"})

        alice.message("#study", "first meeting is friday")

        assert wait_until(lambda: "first meeting is friday" in bodies(bob_in))
        assert wait_until(lambda: "first meeting is friday" in bodies(carol_in))
    finally:
        for conn in (alice, bob, carol):
            conn.close()


def test_somebody_invited_later_gets_the_next_message(server: ChatServer) -> None:
    alice_in: list[Frame] = []
    bob_in: list[Frame] = []

    alice = join(server, "alice", alice_in)
    bob = join(server, "bob", bob_in)

    try:
        alice.create_room("#study")
        assert wait_until(lambda: server.rooms.members("#study") == {"alice"})

        alice.invite("#study", ["bob"])
        assert wait_until(lambda: "bob" in server.rooms.members("#study"))

        alice.message("#study", "you are in")
        assert wait_until(lambda: "you are in" in bodies(bob_in))
    finally:
        for conn in (alice, bob):
            conn.close()


def test_a_session_forwards_everything_a_connection_can_send() -> None:
    """Session must offer exactly what ServerConnection offers.

    This is here because it has now gone wrong twice. Session stands in for a
    ServerConnection so the controller never notices a reconnect, but the
    forwarding is written by hand -- so a method added to ServerConnection is
    simply absent from Session until somebody remembers. Nothing catches it:
    the controller tests use a fake that has every method, the integration
    tests drive a ServerConnection directly, and the one caller that would
    notice is inside a try/except.

    The failure is invisible rather than loud. Under pythonw there is no
    console, so the AttributeError goes nowhere and the button appears dead.
    """
    sending = {
        "message",
        "typing",
        "receipt",
        "get_key",
        "history",
        "create_room",
        "invite",
        "join",
        "leave",
        "ping",
    }
    missing = {name for name in sending if not hasattr(Session, name)}
    assert not missing, f"Session cannot forward: {sorted(missing)}"

    for name in sorted(sending):
        theirs = inspect.signature(getattr(ServerConnection, name))
        ours = inspect.signature(getattr(Session, name))
        assert ours == theirs, f"Session.{name}{ours} does not match ServerConnection{theirs}"
