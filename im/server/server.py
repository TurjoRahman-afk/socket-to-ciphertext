"""The listening socket and its accept loop.

Phase 2: accept a connection, wrap it in a ClientHandler, hand it a thread,
and let the router do the rest. The server object itself knows nothing about
usernames, rooms or messages.
"""

from __future__ import annotations

import logging
import socket
import ssl
import threading

from im.server.handler import ClientHandler
from im.server.registries import SessionRegistry
from im.server.router import MessageRouter
from im.server.store.db import MEMORY, Database
from im.server.store.messages import MessageStore
from im.server.store.rooms import SqliteRooms
from im.server.store.users import SqliteUsers

log = logging.getLogger(__name__)

#: How many connections the OS may hold in the accept queue.
BACKLOG = 32


class ChatServer:  # this represents the whole server
    """Accepts connections and routes between them.

    One reader thread and one writer thread per client, both daemons, so a
    stuck client can never keep the process alive after shutdown.
    """

    # by default the server will listen on 127.0.0.1:5000
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        db_path: str = MEMORY,
        tls: ssl.SSLContext | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.db = Database(db_path)
        # None means plaintext. TLS is opt-in so a developer can still
        # point telnet at the port and read the protocol.
        self.tls = tls
        self.sessions = SessionRegistry()
        self.rooms = SqliteRooms(self.db)
        self.users = SqliteUsers(self.db)
        self.messages = MessageStore(self.db)
        self.router = MessageRouter(self.sessions, self.rooms, self.users, self.messages)

        # the listener is none but after bind() it is tcp listening socket
        self._listener: socket.socket | None = None
        self._handlers: set[ClientHandler] = set()
        self._lock = threading.Lock()
        self._running = threading.Event()

    @property
    def address(self) -> tuple[str, int]:
        """The bound address. Reads the real port back when port 0 was asked
        for, which is how tests get a free port without racing."""
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

    def serve_forever(self) -> None:  # THE heart of the server
        """Accept connections until shutdown() is called."""
        if self._listener is None:
            self.bind()
        assert self._listener is not None

        while self._running.is_set():
            try:
                conn, peer = self._listener.accept()
            except OSError:
                # Expected: shutdown() closes the listening socket, which
                # makes the blocking accept() fail.
                break

            if self.tls is not None:
                try:
                    conn = self.tls.wrap_socket(conn, server_side=True)
                except (ssl.SSLError, OSError) as exc:
                    # A failed handshake is one client's problem, not the
                    # server's. Log it and carry on accepting.
                    log.warning("TLS handshake failed for %s:%s: %s", *peer, exc)
                    conn.close()
                    continue

            handler = ClientHandler(conn, peer, self.router)
            with self._lock:
                self._handlers.add(handler)
            threading.Thread(
                target=self._run_handler,
                args=(handler,),
                name=f"reader-{handler.name}",
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
            handlers, self._handlers = self._handlers, set()
        for handler in handlers:
            handler.close()
        log.info("server stopped")

    def _run_handler(self, handler: ClientHandler) -> None:
        try:
            handler.serve()
        finally:
            with self._lock:
                self._handlers.discard(handler)
