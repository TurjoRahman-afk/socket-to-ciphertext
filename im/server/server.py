"""The listening socket and its accept loop.

Phase 1 is an echo server: it proves the socket setup, the per-client thread
model and the codec all work together, and it gives two telnet sessions
something to talk to. Phase 2 replaces the echo with the real router.
"""

from __future__ import annotations

import logging
import socket
import threading

from im.common.codec import LineBuffer, decode, encode
from im.common.frames import Frame, MessageType, ProtocolError, error

log = logging.getLogger(__name__)

#: How many connections the OS may hold in the accept queue.
BACKLOG = 32

#: Read size. Nothing about the protocol depends on this -- LineBuffer puts
#: the stream back together regardless of how it happens to be chopped up.
RECV_BYTES = 4096


class EchoServer:
    """Accepts connections and echoes every frame back to its sender.

    One thread per client, as the assignment asks for. Threads are daemons so
    that a stuck client can never keep the process alive after shutdown.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5000) -> None:
        self.host = host
        self.port = port
        self._listener: socket.socket | None = None
        self._clients: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._running = threading.Event()

    @property
    def address(self) -> tuple[str, int]:
        """The bound address. Reads the real port back when port 0 was asked
        for, which is how the tests get a free port without racing."""
        if self._listener is None:
            raise RuntimeError("server is not bound yet")
        host, port = self._listener.getsockname()[:2]
        return host, port

    def bind(self) -> tuple[str, int]:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Without SO_REUSEADDR the port sits in TIME_WAIT for a minute or two
        # after a restart and binding fails with "Address already in use".
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(BACKLOG)
        self._listener = listener
        self._running.set()
        log.info("listening on %s:%s", *self.address)
        return self.address

    def serve_forever(self) -> None:
        """Accept connections until shutdown() is called."""
        if self._listener is None:
            self.bind()
        assert self._listener is not None

        while self._running.is_set():
            try:
                conn, peer = self._listener.accept()
            except OSError:
                # Expected: shutdown() closes the listening socket, which makes
                # the blocking accept() fail. Anything else and we are stopping
                # anyway.
                break
            with self._lock:
                self._clients.add(conn)
            threading.Thread(
                target=self._serve_client,
                args=(conn, peer),
                name=f"client-{peer[0]}:{peer[1]}",
                daemon=True,
            ).start()

    def shutdown(self) -> None:
        """Stop accepting, and hang up on everyone still connected."""
        self._running.clear()
        if self._listener is not None:
            # Closing the listener is what breaks the blocking accept() above.
            self._listener.close()
            self._listener = None
        with self._lock:
            clients, self._clients = self._clients, set()
        for conn in clients:
            conn.close()
        log.info("server stopped")

    def _serve_client(self, conn: socket.socket, peer: tuple[str, int]) -> None:
        log.info("connected %s:%s", *peer)
        buffer = LineBuffer()
        try:
            while True:
                chunk = conn.recv(RECV_BYTES)
                if not chunk:
                    break  # peer closed the connection
                try:
                    lines = buffer.feed(chunk)
                except ProtocolError as exc:
                    self._send(conn, error("LINE_TOO_LONG", str(exc)))
                    break
                for line in lines:
                    if not self._handle_line(conn, line):
                        return
        except OSError:
            pass  # connection reset, or closed under us by shutdown()
        finally:
            with self._lock:
                self._clients.discard(conn)
            conn.close()
            log.info("disconnected %s:%s", *peer)

    def _handle_line(self, conn: socket.socket, line: str) -> bool:
        """Echo one line back. Returns False when the connection should close."""
        try:
            frame = decode(line)
        except ProtocolError as exc:
            # Telnet users typing plain text land here, which is exactly the
            # feedback they need. A real client would be closed on; in phase 1
            # we stay connected so the session is usable by hand.
            self._send(conn, error("BAD_FRAME", str(exc)))
            return True

        if frame.type is MessageType.PING:
            return self._send(conn, Frame(type=MessageType.PONG))
        return self._send(conn, frame)

    @staticmethod
    def _send(conn: socket.socket, frame: Frame) -> bool:
        try:
            conn.sendall(encode(frame))
        except OSError:
            return False
        return True
