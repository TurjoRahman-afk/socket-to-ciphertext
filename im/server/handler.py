"""One connection: a reader thread, an outbound queue, and a writer thread.

The queue is the entire point of this file. Without it the router would call
sendall() itself, and a single client on a slow link would block the thread
that is trying to deliver to everybody else -- the "messages arrive late or
never" failure. Instead the router only ever puts a frame on a queue and
returns immediately; the blocking part happens on a thread this connection
owns and nobody else waits on.

Two threads per client, then: one blocked in recv(), one blocked in get().
"""

from __future__ import annotations

import logging
import queue
import socket
import threading

from im.common.codec import LineBuffer, decode, encode
from im.common.frames import Frame, ProtocolError, error

log = logging.getLogger(__name__)

RECV_BYTES = 4096

#: How many frames may pile up for one client before we give up on it. A
#: client too slow to drain this is not going to recover, and an unbounded
#: queue would let one stalled connection consume the server's memory.
OUTBOX_LIMIT = 1000

#: Sentinel that tells the writer thread to finish.
_STOP = object()


class ClientHandler:
    """One client. Implements the Session protocol the router talks to."""

    def __init__(self, conn: socket.socket, peer: tuple[str, int], router) -> None:
        self.username: str | None = None
        self._conn = conn
        self._peer = peer
        self._router = router
        self._outbox: queue.Queue = queue.Queue(maxsize=OUTBOX_LIMIT)
        self._closed = threading.Event()
        self._writer = threading.Thread(
            target=self._write_loop,
            name=f"writer-{self.name}",
            daemon=True,
        )

    @property
    def name(self) -> str:
        return self.username or f"{self._peer[0]}:{self._peer[1]}"

    # ---------------------------------------------------- Session protocol ---

    def send(self, frame: Frame) -> None:
        """Hand a frame to this client's writer thread. Never blocks.

        Called by the router from *another* client's reader thread, which is
        why it must not touch the socket.
        """
        if self._closed.is_set():
            return
        try:
            self._outbox.put_nowait(frame)
        except queue.Full:
            log.warning("outbox full for %s, dropping the connection", self.name)
            self.close()

    # ------------------------------------------------------------ lifecycle ---

    def serve(self) -> None:
        """Run this connection until its socket closes. Blocks the caller."""
        self._writer.start()
        log.info("connected %s", self.name)
        try:
            self._read_loop()
        finally:
            # on_disconnect first, so the OFFLINE announcement still goes out
            # while the registries know who this was.
            self._router.on_disconnect(self)
            self.close()
            self._writer.join(timeout=2)
            log.info("disconnected %s", self.name)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._outbox.put_nowait(_STOP)
        except queue.Full:
            pass  # Closing the socket below stops the writer regardless.
        try:
            self._conn.close()
        except OSError:
            pass

    # ---------------------------------------------------------------- loops ---

    def _read_loop(self) -> None:
        buffer = LineBuffer()
        while not self._closed.is_set():
            try:
                chunk = self._conn.recv(RECV_BYTES)
            except OSError:
                return  # Reset, or closed under us by close().
            if not chunk:
                return  # Peer hung up.

            try:
                lines = buffer.feed(chunk)
            except ProtocolError as exc:
                self.send(error("LINE_TOO_LONG", str(exc)))
                return

            for line in lines:
                try:
                    frame = decode(line)
                except ProtocolError as exc:
                    # Malformed input is the peer's problem, not a reason to
                    # hang up: a telnet session typing prose stays usable.
                    self.send(error("BAD_FRAME", str(exc)))
                    continue
                self._router.handle(self, frame)

    def _write_loop(self) -> None:
        """The only place this connection's socket is written to."""
        while True:
            item = self._outbox.get()
            if item is _STOP:
                return

            try:
                wire = encode(item)
            except Exception:
                # A frame this server built is malformed. Skipping it loses one
                # message; letting the exception escape would kill this thread
                # and leave the connection open but permanently mute, which is
                # far harder to diagnose.
                log.exception("could not encode a frame for %s, skipping it", self.name)
                continue

            try:
                self._conn.sendall(wire)
            except OSError:
                self._closed.set()
                return
