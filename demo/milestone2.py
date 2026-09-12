"""Milestone 2 demonstration. Run it in front of the marker.

    python -m demo.milestone2 framing
    python -m demo.milestone2 concurrency
    python -m demo.milestone2 encryption
    python -m demo.milestone2 all

Each one targets a part of the design that could quietly be wrong -- where a
bug does not announce itself with a stack trace, but shows up as a message
that arrives mangled, late, or not at all, on somebody else's machine, once a
week. Those are the parts worth proving rather than asserting.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

from im.client.net.connection import ServerConnection
from im.common.codec import LineBuffer, decode, encode
from im.common.frames import Frame, MessageType
from im.crypto.identity import Identity
from im.crypto.keyring import Keyring
from im.server.server import ChatServer

HASH = "pretend-digest"


def rule(title: str) -> None:
    print(f"\n{'=' * 72}\n  {title}\n{'=' * 72}")


def step(text: str) -> None:
    print(f"\n  {text}")


# ---------------------------------------------------------------- framing ---


def framing() -> None:
    """Risk: TCP is a byte stream, and one recv() is not one message.

    This is the bug that produces "half a message" and "two messages stuck
    together" in almost every first attempt at a socket protocol, and it only
    shows up once the network is slow or the messages are large.
    """
    rule("1. TCP HAS NO MESSAGE BOUNDARIES")

    original = Frame(type=MessageType.MSG, sender="aya", to="keisha", body="hello 你好 🔐")
    wire = encode(original)
    print(f"\n  one frame on the wire, {len(wire)} bytes:")
    print(f"    {wire!r}")

    step("delivered one byte at a time, as a slow link would:")
    buffer = LineBuffer()
    lines = [line for byte in wire for line in buffer.feed(bytes([byte]))]
    print(f"    complete frames recovered: {len(lines)}")
    print(f"    body: {decode(lines[0]).body}")

    step("three frames arriving in a single read, as a fast one would:")
    buffer = LineBuffer()
    burst = b"".join(encode(Frame(type=MessageType.MSG, body=f"message {i}")) for i in range(3))
    lines = buffer.feed(burst)
    print(f"    one recv() of {len(burst)} bytes produced {len(lines)} frames:")
    for line in lines:
        print(f"      {decode(line).body}")

    step("a multi-byte character cut in half between two reads:")
    buffer = LineBuffer()
    wire = encode(Frame(type=MessageType.MSG, body="你好"))
    cut = wire.index("你".encode()) + 1
    print(f"    first read ends mid-character, at byte {cut}")
    print(f"    frames after the first read:  {len(buffer.feed(wire[:cut]))}")
    lines = buffer.feed(wire[cut:])
    print(f"    frames after the second read: {len(lines)}")
    print(f"    body: {decode(lines[0]).body}")


# ------------------------------------------------------------ concurrency ---


def concurrency() -> None:
    """Risk: threads sharing state, and one slow client blocking everybody.

    The highest-risk area in the whole design. A missing lock corrupts a
    registry once in a thousand logins; a blocking write in the router means
    one person on hotel wifi freezes the conversation for everyone else.
    Neither fails loudly.
    """
    rule("2. TWENTY CLIENTS AT ONCE, AND NOBODY BLOCKS ANYBODY")

    server = ChatServer("127.0.0.1", 0)
    host, port = server.bind()
    threading.Thread(target=server.serve_forever, daemon=True).start()

    count = 20
    inboxes: list[list[Frame]] = [[] for _ in range(count)]
    # Indexed, not appended. Threads finish in whatever order they finish,
    # so appending would leave clients[i] and inboxes[i] describing
    # different people -- which is exactly what made the first run of this
    # demo report 18 of 19 deliveries.
    clients: list[ServerConnection | None] = [None] * count

    try:
        step(f"connecting {count} clients simultaneously...")
        started = time.perf_counter()
        threads = []

        def connect(i: int) -> None:
            conn = ServerConnection(host, port, on_frame=inboxes[i].append)
            conn.connect()
            conn.register(f"user{i:02d}", HASH)
            conn.login(f"user{i:02d}", HASH)
            clients[i] = conn

        for i in range(count):
            thread = threading.Thread(target=connect, args=(i,))
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join(timeout=20)

        elapsed = time.perf_counter() - started
        print(f"    {sum(1 for c in clients if c is not None)} logged in, in {elapsed:.2f}s")
        print(f"    server threads now running: {threading.active_count()} in this process")
        print(f"    session registry holds: {len(server.sessions)} users")
        print("    -> every login is a compound check-then-claim under one lock.")
        print("       Without it, two clients taking the same name both succeed.")

        step("all twenty join one room, then one message is fanned out:")
        clients[0].create_room("#demo")
        time.sleep(0.4)
        for client in clients[1:]:
            client.join("#demo")
        time.sleep(1.5)
        print(f"    room membership: {len(server.rooms.members('#demo'))}")

        started = time.perf_counter()
        clients[0].message("#demo", "one message, nineteen recipients")
        deadline = time.perf_counter() + 10
        while time.perf_counter() < deadline:
            arrived = sum(
                1 for inbox in inboxes[1:] if any(f.type is MessageType.MSG for f in inbox)
            )
            if arrived >= count - 1:
                break
            time.sleep(0.02)
        print(f"    delivered to {arrived}/{count - 1} in {time.perf_counter() - started:.3f}s")
        print("    -> the router never touched a socket. It put the frame on")
        print("       nineteen queues and returned. Each connection's own writer")
        print("       thread did the blocking part, so no client waits on another.")

        step("ordering under load: 100 messages down one connection")
        inbox = inboxes[1]
        inbox.clear()
        for i in range(100):
            clients[0].message("user01", f"{i:03d}")
        deadline = time.perf_counter() + 15
        while time.perf_counter() < deadline:
            got = [f.body for f in inbox if f.type is MessageType.MSG]
            if len(got) >= 100:
                break
            time.sleep(0.02)
        in_order = got == [f"{i:03d}" for i in range(100)]
        print(f"    received {len(got)}/100, in the order sent: {in_order}")
        print("    -> one TCP stream, one writer thread, one queue. Ordering holds.")
    finally:
        for client in clients:
            if client is not None:
                client.close()
        server.shutdown()


# ------------------------------------------------------------- encryption ---


def encryption() -> None:
    """Risk: claiming the server cannot read messages, and being wrong.

    An overclaim a marker can puncture is worse than no claim. So this shows
    the database, not a diagram.
    """
    rule("3. THE SERVER STORES WHAT IT CANNOT READ")

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "demo.db")
        server = ChatServer("127.0.0.1", 0, db_path=db)
        host, port = server.bind()
        threading.Thread(target=server.serve_forever, daemon=True).start()

        aya = Keyring(Identity.generate())
        keisha = Keyring(Identity.generate())
        received: list[Frame] = []

        try:
            a = ServerConnection(host, port)
            a.connect()
            a.register("aya", HASH, pubkey=aya.public_b64)
            a.login("aya", HASH)

            k = ServerConnection(host, port, on_frame=received.append)
            k.connect()
            k.register("keisha", HASH, pubkey=keisha.public_b64)
            k.login("keisha", HASH)

            secret = "the secret word is swordfish 🔐"
            step(f'aya types:  "{secret}"')

            aya.remember("keisha", keisha.public_b64)
            ciphertext, nonce = aya.seal("keisha", secret, "aya")
            a.message("keisha", ciphertext, nonce=nonce)
            time.sleep(0.8)

            print("\n  what travels on the wire:")
            print(f"    body  = {ciphertext}")
            print(f"    nonce = {nonce}")

            keisha.remember("aya", aya.public_b64)
            delivered = [f for f in received if f.type is MessageType.MSG][0]
            plain = keisha.open("aya", delivered.body, delivered.nonce, delivered.sender)
            step(f'keisha reads: "{plain}"')

            time.sleep(0.4)
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT sender, recipient, body FROM messages").fetchone()
            print("\n  what the SERVER has on disk:")
            print(f"    {row['sender']} -> {row['recipient']}")
            print(f"    body = {row['body']}")
            print(
                f"\n    'swordfish' present anywhere in the server's copy: "
                f"{'swordfish' in (row['body'] or '')}"
            )
            print("    -> the hub routed a message it cannot read. The private")
            print("       keys never left the two clients.")
            conn.close()
        finally:
            for c in (a, k):
                c.close()
            server.shutdown()
            # Windows will not delete a file that is still open, and the
            # temporary directory is removed on the way out of this block.
            server.db.close()


def main(argv: list[str]) -> int:
    logging.disable(logging.CRITICAL)
    choice = argv[1] if len(argv) > 1 else "all"
    demos = {"framing": framing, "concurrency": concurrency, "encryption": encryption}

    if choice == "all":
        for run in demos.values():
            run()
    elif choice in demos:
        demos[choice]()
    else:
        print(f"usage: python -m demo.milestone2 [{' | '.join(demos)} | all]")
        return 1

    print("\n" + "=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
