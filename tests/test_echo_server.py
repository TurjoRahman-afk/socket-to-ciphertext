"""Echo server tests. Phase 1.

The automated form of the phase 1 exit criteria: two clients connected at the
same time, each getting its own frames back. Real sockets, bound to port 0 so
the OS picks a free port and the suite never collides with a running server.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator

import pytest

from im.common.codec import LineBuffer, decode, encode
from im.common.frames import Frame, MessageType
from im.server.server import EchoServer


@pytest.fixture
def server() -> Iterator[tuple[str, int]]:
    server = EchoServer("127.0.0.1", 0)
    address = server.bind()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield address
    finally:
        server.shutdown()
        thread.join(timeout=2)


class Client:
    """A minimal test client. Deliberately not the real one -- phase 1 has no
    client yet, and these tests exercise the server on its own."""

    def __init__(self, address: tuple[str, int]) -> None:
        self.sock = socket.create_connection(address, timeout=5)
        self.buffer = LineBuffer()

    def send(self, frame: Frame) -> None:
        self.sock.sendall(encode(frame))

    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def receive(self) -> Frame:
        while True:
            lines = self.buffer.feed(self.sock.recv(4096))
            if lines:
                return decode(lines[0])

    def close(self) -> None:
        self.sock.close()


def test_a_frame_comes_back(server: tuple[str, int]) -> None:
    client = Client(server)
    try:
        sent = Frame(type=MessageType.MSG, sender="alice", body="hello 你好 🔐")
        client.send(sent)
        echoed = client.receive()

        assert echoed.id == sent.id
        assert echoed.body == "hello 你好 🔐"
    finally:
        client.close()


def test_two_clients_are_served_at_once(server: tuple[str, int]) -> None:
    """The exit criteria, automated: two concurrent sessions, no crosstalk."""
    alice, bob = Client(server), Client(server)
    try:
        alice.send(Frame(type=MessageType.MSG, sender="alice", body="from alice"))
        bob.send(Frame(type=MessageType.MSG, sender="bob", body="from bob"))

        assert alice.receive().body == "from alice"
        assert bob.receive().body == "from bob"
    finally:
        alice.close()
        bob.close()


def test_ping_is_answered_with_pong(server: tuple[str, int]) -> None:
    client = Client(server)
    try:
        client.send(Frame(type=MessageType.PING))
        assert client.receive().type is MessageType.PONG
    finally:
        client.close()


def test_plain_text_gets_an_error_rather_than_a_dropped_connection(
    server: tuple[str, int],
) -> None:
    """What a telnet user sees when they type prose instead of JSON."""
    client = Client(server)
    try:
        client.send_raw(b"hello, is this thing on?\n")
        reply = client.receive()

        assert reply.type is MessageType.ERROR
        assert reply.data["code"] == "BAD_FRAME"

        # Still connected: the next well-formed frame works.
        client.send(Frame(type=MessageType.PING))
        assert client.receive().type is MessageType.PONG
    finally:
        client.close()


def test_a_frame_sent_in_pieces_still_echoes(server: tuple[str, int]) -> None:
    client = Client(server)
    try:
        wire = encode(Frame(type=MessageType.MSG, body="pieces"))
        for byte in wire:
            client.send_raw(bytes([byte]))
        assert client.receive().body == "pieces"
    finally:
        client.close()


def test_the_port_is_reusable_immediately_after_shutdown() -> None:
    """SO_REUSEADDR, tested: without it this second bind fails with
    'Address already in use' while the port sits in TIME_WAIT."""
    first = EchoServer("127.0.0.1", 0)
    host, port = first.bind()
    thread = threading.Thread(target=first.serve_forever, daemon=True)
    thread.start()
    Client((host, port)).close()
    first.shutdown()
    thread.join(timeout=2)

    second = EchoServer(host, port)
    try:
        assert second.bind() == (host, port)
    finally:
        second.shutdown()
