"""A connection that comes back by itself.

A ServerConnection is deliberately not reusable -- CLOSED is terminal, which
is what keeps its state machine honest. Reconnecting therefore means building
a new one, and something has to own the credentials and decide when to try.
That is this class.

It offers the same sending methods as a ServerConnection, so the controller
holds one of these instead and never learns that the socket underneath it has
been replaced.

    Session  ──owns──▶  ServerConnection  (replaced on every reconnect)
        │
        └──runs──▶  supervisor thread: waits out the backoff, reconnects,
                    logs in again, and resets the schedule on success
"""

from __future__ import annotations

import logging
import ssl
import threading
from collections.abc import Callable

from im.client.net.backoff import Backoff
from im.client.net.connection import NotConnected, ServerConnection
from im.client.net.state import ConnectionState
from im.common.frames import Frame, MessageType

log = logging.getLogger(__name__)


class Session:
    """Keeps one user logged in, across as many sockets as that takes."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        pass_hash: str,
        *,
        pubkey: str | None = None,
        tls: ssl.SSLContext | None = None,
        server_hostname: str | None = None,
        on_frame: Callable[[Frame], None] | None = None,
        on_state: Callable[[ConnectionState], None] | None = None,
        heartbeat_seconds: float | None = None,
        backoff: Backoff | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.pass_hash = pass_hash
        self.pubkey = pubkey
        self.tls = tls
        self.server_hostname = server_hostname
        self.on_frame = on_frame
        self.on_state = on_state
        self.heartbeat_seconds = heartbeat_seconds
        self.backoff = backoff or Backoff()

        self._connection: ServerConnection | None = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._supervisor: threading.Thread | None = None
        self.reconnects = 0

    # ------------------------------------------------------------- lifecycle ---

    def start(self, register: bool = False) -> Frame:
        """Connect and log in. Returns the LOGIN_OK or ERROR frame.

        Raises rather than retrying if the server cannot be reached at all the
        first time: a wrong address should be reported, not retried silently
        forever.
        """
        connection = self._build()
        connection.connect()

        if register:
            reply = connection.register(self.username, self.pass_hash, pubkey=self.pubkey)
            # An account that already exists is not a failure to register --
            # it is the second time somebody ran the same command. Anything
            # else (a bad username, a missing field) still stops here.
            if reply.type is MessageType.ERROR and reply.data.get("code") != "USER_EXISTS":
                connection.close()
                return reply

        reply = connection.login(self.username, self.pass_hash)
        if reply.type is MessageType.ERROR:
            connection.close()
            return reply

        with self._lock:
            self._connection = connection
        self.backoff.reset()
        self._start_supervisor()
        return reply

    def close(self) -> None:
        self._stopped.set()
        with self._lock:
            connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    @property
    def connection(self) -> ServerConnection | None:
        with self._lock:
            return self._connection

    @property
    def state(self) -> ConnectionState:
        connection = self.connection
        return ConnectionState.DISCONNECTED if connection is None else connection.state

    @property
    def can_send(self) -> bool:
        connection = self.connection
        return connection is not None and connection.can_send

    # ------------------------------------------------------------ supervisor ---

    def _build(self) -> ServerConnection:
        kwargs = {}
        if self.heartbeat_seconds is not None:
            kwargs["heartbeat_seconds"] = self.heartbeat_seconds
        return ServerConnection(
            self.host,
            self.port,
            on_frame=self.on_frame,
            on_state=self.on_state,
            tls=self.tls,
            server_hostname=self.server_hostname,
            **kwargs,
        )

    def _start_supervisor(self) -> None:
        if self._supervisor is not None:
            return
        self._supervisor = threading.Thread(
            target=self._watch, name="session-supervisor", daemon=True
        )
        self._supervisor.start()

    def _watch(self) -> None:
        """Rebuild the connection whenever it stops being usable.

        Polled rather than driven by the state observer, because the observer
        runs on whichever thread noticed the failure -- often the reader
        thread that is about to die. Reconnecting from there would mean a
        thread outliving the connection that owns it.
        """
        while not self._stopped.wait(0.1):
            connection = self.connection

            if connection is not None:
                if connection.can_send:
                    continue  # healthy
                if connection.state in (
                    ConnectionState.CONNECTING,
                    ConnectionState.AUTHENTICATING,
                ):
                    continue  # a handshake is already in flight

            # Anything else -- RETRYING, CLOSED, or nothing at all -- means we
            # owe a reconnection. Testing for RETRYING alone was a bug: a
            # failed retry leaves a CLOSED connection behind, and the loop
            # then waited for a state that could never come back.
            delay = self.backoff.next_delay()
            log.info("not connected; next attempt in %.1fs", delay)
            if self._stopped.wait(delay):
                return
            self._reconnect()

    def _reconnect(self) -> None:
        with self._lock:
            dead, self._connection = self._connection, None
        if dead is not None:
            dead.close()

        fresh = self._build()
        try:
            fresh.connect()
            reply = fresh.login(self.username, self.pass_hash)
        except (ConnectionError, TimeoutError, OSError) as exc:
            log.info("reconnect failed: %s", exc)
            fresh.close()
            return

        if reply.type is MessageType.ERROR:
            # The credentials worked a moment ago, so this is the server
            # refusing for another reason -- the old session still counted as
            # online, most likely. Let the backoff carry us to the next try.
            log.info("reconnect refused: %s", reply.data.get("message"))
            fresh.close()
            return

        with self._lock:
            self._connection = fresh
        self.reconnects += 1
        self.backoff.reset()
        log.info("reconnected as %s", self.username)

    # --------------------------------------------------------------- sending ---
    # The same surface as ServerConnection, so the controller never notices
    # that the socket underneath it has been replaced.

    def _live(self) -> ServerConnection:
        connection = self.connection
        if connection is None or not connection.can_send:
            raise NotConnected("not connected")
        return connection

    def message(self, to: str, body: str, nonce: str | None = None) -> Frame:
        return self._live().message(to, body, nonce=nonce)

    def typing(self, to: str, on: bool = True) -> None:
        self._live().typing(to, on)

    def get_key(self, user: str) -> None:
        self._live().get_key(user)

    def history(self, room: str, before: int | None = None, limit: int = 50) -> None:
        self._live().history(room, before=before, limit=limit)

    def receipt(self, to: str, ref: str, state: str) -> None:
        self._live().receipt(to, ref, state)

    def create_room(self, room: str, members: list[str] | None = None) -> None:
        self._live().create_room(room, members)

    def invite(self, room: str, members: list[str]) -> None:
        self._live().invite(room, members)

    def join(self, room: str) -> None:
        self._live().join(room)

    def leave(self, room: str) -> None:
        self._live().leave(room)

    def ping(self) -> None:
        self._live().ping()
