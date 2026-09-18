"""Measure what this server actually does, rather than guessing.

    python -m demo.bench latency      round trip and delivery, when idle
    python -m demo.bench throughput   messages per second through one server
    python -m demo.bench connections  raise the client count until it breaks

Numbers from one machine talking to itself over loopback. Real latency adds
the network, which for two people in the same city is a further 10-40ms and
dwarfs everything measured here. That is the point: the server is not what
makes a chat feel slow until it is very heavily loaded.
"""

from __future__ import annotations

import logging
import sys
import threading
import time

from im.client.net.connection import ServerConnection
from im.common.frames import Frame, MessageType
from im.server.server import ChatServer

HASH = "bench"


def rule(title: str) -> None:
    print(f"\n{'=' * 72}\n  {title}\n{'=' * 72}")


def start_server() -> tuple[ChatServer, tuple[str, int]]:
    server = ChatServer("127.0.0.1", 0)
    address = server.bind()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, address


def join(address: tuple[str, int], user: str, inbox: list | None = None) -> ServerConnection:
    conn = ServerConnection(*address, on_frame=(inbox.append if inbox is not None else None))
    conn.connect()
    conn.register(user, HASH)
    conn.login(user, HASH)
    return conn


def percentiles(samples: list[float]) -> str:
    ordered = sorted(samples)

    def pick(p: float) -> float:
        return ordered[min(len(ordered) - 1, int(len(ordered) * p))]

    return (
        f"min {ordered[0]:6.2f}ms   p50 {pick(0.50):6.2f}ms   "
        f"p95 {pick(0.95):6.2f}ms   p99 {pick(0.99):6.2f}ms   max {ordered[-1]:6.2f}ms"
    )


# ---------------------------------------------------------------- latency ---


def latency(rounds: int = 300) -> None:
    rule("LATENCY (loopback, server otherwise idle)")
    server, address = start_server()
    alice_in: list[Frame] = []
    bob_in: list[Frame] = []
    alice = join(address, "alice", alice_in)
    bob = join(address, "bob", bob_in)

    try:
        # PING -> PONG. The smallest possible round trip: no routing, no
        # storage, just the two threads and the socket.
        pongs: list[float] = []
        for _ in range(rounds):
            bob_in.clear()
            alice_in.clear()
            started = time.perf_counter()
            alice.ping()
            while not any(f.type is MessageType.PONG for f in alice_in):
                time.sleep(0.0002)
            pongs.append((time.perf_counter() - started) * 1000)
        print(f"\n  PING -> PONG           {percentiles(pongs)}")

        # alice -> server -> bob. Adds routing, a database write and a second
        # client's queue and writer thread.
        deliveries: list[float] = []
        for i in range(rounds):
            bob_in.clear()
            started = time.perf_counter()
            alice.message("bob", f"m{i}")
            while not any(f.type is MessageType.MSG for f in bob_in):
                time.sleep(0.0002)
            deliveries.append((time.perf_counter() - started) * 1000)
        print(f"  alice -> bob           {percentiles(deliveries)}")

        print("\n  A message is stored and delivered in well under a millisecond.")
        print("  Over a real network the link adds 10-40ms, so the server is not")
        print("  what a user perceives as delay until it is heavily loaded.")
    finally:
        alice.close()
        bob.close()
        server.shutdown()


# ------------------------------------------------------------- throughput ---


def throughput(senders: int = 25, each: int = 200) -> None:
    rule("THROUGHPUT (direct messages, one server)")
    server, address = start_server()
    sink_in: list[Frame] = []
    sink = join(address, "sink", sink_in)
    clients = [join(address, f"send{i:02d}") for i in range(senders)]

    try:
        total = senders * each
        started = time.perf_counter()

        def blast(client: ServerConnection) -> None:
            for n in range(each):
                client.message("sink", f"x{n}")

        threads = [threading.Thread(target=blast, args=(c,)) for c in clients]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        deadline = time.perf_counter() + 30
        while (
            sum(1 for f in sink_in if f.type is MessageType.MSG) < total
            and time.perf_counter() < deadline
        ):
            time.sleep(0.01)
        elapsed = time.perf_counter() - started
        received = sum(1 for f in sink_in if f.type is MessageType.MSG)

        print(f"\n  {senders} senders x {each} messages = {total}")
        print(f"  received {received}/{total} in {elapsed:.2f}s")
        print(f"  {received / elapsed:,.0f} messages/second through one server")
        print("\n  Every one is written to sqlite before delivery, so this is")
        print("  the storage rate as much as the routing rate.")
    finally:
        for client in [*clients, sink]:
            client.close()
        server.shutdown()


# ------------------------------------------------------------ connections ---


def connections(steps: tuple[int, ...] = (50, 100, 200, 400, 800)) -> None:
    rule("CONNECTIONS (how many before it stops coping)")
    print("\n  Each client costs two threads on the server and two here, so this")
    print("  measures the thread-per-connection model, not the protocol.\n")
    print(f"  {'clients':>8}  {'connected':>10}  {'time':>8}  {'threads':>8}  {'fan-out':>9}")
    print(f"  {'-' * 8}  {'-' * 10}  {'-' * 8}  {'-' * 8}  {'-' * 9}")

    for count in steps:
        server, address = start_server()
        inboxes: list[list[Frame]] = [[] for _ in range(count)]
        clients: list[ServerConnection | None] = [None] * count
        failures: list[str] = []

        # Bound as defaults rather than captured: the closure outlives the
        # loop iteration that made it, and a late-binding capture would have
        # every thread reading whatever the last iteration left behind.
        def connect(
            i: int,
            address=address,
            clients=clients,
            inboxes=inboxes,
            failures=failures,
        ) -> None:
            try:
                clients[i] = join(address, f"u{i:04d}", inboxes[i])
            except Exception as exc:  # noqa: BLE001 -- the number is the point
                failures.append(type(exc).__name__)

        try:
            started = time.perf_counter()
            threads = [threading.Thread(target=connect, args=(i,)) for i in range(count)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=120)
            elapsed = time.perf_counter() - started
            live = [c for c in clients if c is not None]

            fan = "-"
            if len(live) >= 2:
                started = time.perf_counter()
                live[0].message(live[1].username, "ping")
                deadline = time.perf_counter() + 10
                target = inboxes[clients.index(live[1])]
                while (
                    not any(f.type is MessageType.MSG for f in target)
                    and time.perf_counter() < deadline
                ):
                    time.sleep(0.001)
                fan = f"{(time.perf_counter() - started) * 1000:.1f}ms"

            note = f"  ({len(failures)} failed: {failures[0]})" if failures else ""
            print(
                f"  {count:>8}  {len(live):>10}  {elapsed:>7.2f}s  "
                f"{threading.active_count():>8}  {fan:>9}{note}"
            )
            if failures:
                print("\n  -> this is the ceiling of one process using two threads")
                print("     per connection. See the notes in the README.")
                break
        finally:
            for client in clients:
                if client is not None:
                    client.close()
            server.shutdown()
            time.sleep(0.5)


def main(argv: list[str]) -> int:
    logging.disable(logging.CRITICAL)
    choice = argv[1] if len(argv) > 1 else "all"
    benches = {"latency": latency, "throughput": throughput, "connections": connections}
    if choice == "all":
        for run in benches.values():
            run()
    elif choice in benches:
        benches[choice]()
    else:
        print(f"usage: python -m demo.bench [{' | '.join(benches)} | all]")
        return 1
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
