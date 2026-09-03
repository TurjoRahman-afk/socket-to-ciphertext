"""ServerConnection -- the client's end of the socket.

Mirrors the server's handler: one reader thread, one writer thread, and a
bounded queue between the caller and the socket. Sending never blocks the
caller, so a stalled server cannot freeze the user interface -- which matters
much more here than on the server, because in phase 7 the caller is Tkinter's
main loop.

Everything above this class deals in Frames and states, never in sockets.

Threading rules, which the whole client depends on:

  - Only the reader thread calls recv(). Only the writer thread calls
    sendall(). Nothing else touches the socket.
  - `on_frame` is invoked ON THE READER THREAD. A console view may render
    from there; a Tkinter view must not, and instead pushes onto a
    queue.Queue that the main thread drains with root.after().
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
from collections.abc import Callable

from im.client.net.state import ConnectionState, ConnectionStateMachine, Event
from im.common.codec import LineBuffer, decode, encode
from im.common.frames import Frame, MessageType, ProtocolError

log = logging.getLogger(__name__)

RECV_BYTES = 4096

#: Frames allowed to queue up for the server before we give up. Far smaller
#: than the server's limit: a client with a hundred unsent messages has a
#: broken connection, not a busy one.
OUTBOX_LIMIT = 100

#: How long login() and register() wait for the server to answer.
REPLY_TIMEOUT = 5.0

_STOP = object()


class NotConnected(Exception):
    """Sending was attempted while the connection was not ONLINE."""


class ServerConnection:
    """A connection to one server.

    Not reusable: once closed, build another. That keeps the state machine
    honest -- CLOSED really is terminal.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        on_frame: Callable[[Frame], None] | None = None,
        on_state: Callable[[ConnectionState], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username: str | None = None

        self._on_frame = on_frame
        self._machine = ConnectionStateMachine(on_change=on_state)

        self._sock: socket.socket | None = None
        self._outbox: queue.Queue = queue.Queue(maxsize=OUTBOX_LIMIT)
        self._reader: threading.Thread | None = None
        self._writer: threading.Thread | None = None
        self._closing = threading.Event()

        # One in-flight request at a time. Only the handshake uses this, and
        # REGISTER and LOGIN are strictly sequential, so a single slot is
        # enough -- and far easier to reason about than a map of pending ids.
        self._await_lock = threading.Lock()
        self._await_types: frozenset[MessageType] = frozenset()
        self._reply: Frame | None = None
        self._reply_ready = threading.Event()

    # -------------------------------------------------------------- state ---

    @property
    def state(self) -> ConnectionState:
        return self._machine.state

    @property
    def can_send(self) -> bool:
        return self._machine.can_send

    def listen(
        self,
        on_frame: Callable[[Frame], None] | None = None,
        on_state: Callable[[ConnectionState], None] | None = None,
    ) -> None:
        """Attach or replace the listeners after construction.

        The view is usually built after the connection -- it needs the model,
        which needs nothing -- so this exists to avoid a circular setup, and
        to keep callers out of the private attributes.
        """
        if on_frame is not None:
            self._on_frame = on_frame
        if on_state is not None:
            self._machine._on_change = on_state

    # ----------------------------------------------------------- lifecycle ---

    def connect(self, timeout: float = 5.0) -> None:
        """Open the socket and start both threads. Leaves us AUTHENTICATING."""
        self._machine.transition(Event.CONNECT)
        try:
            sock = socket.create_connection((self.host, self.port), timeout=timeout)
        except OSError as exc:
            self._machine.transition(Event.SOCKET_FAILED)
            raise ConnectionError(f"could not reach {self.host}:{self.port}: {exc}") from exc

        # Back to blocking mode: the connect timeout above must not become a
        # read timeout, or an idle connection would drop every few seconds.
        sock.settimeout(None)
        self._sock = sock

        self._reader = threading.Thread(target=self._read_loop, name="conn-reader", daemon=True)
        self._writer = threading.Thread(target=self._write_loop, name="conn-writer", daemon=True)
        self._reader.start()
        self._writer.start()

        self._machine.transition(Event.SOCKET_READY)
        log.info("connected to %s:%s", self.host, self.port)

    def close(self) -> None:
        """Hang up. Safe to call twice, and from any thread."""
        if self._closing.is_set():
            return
        self._closing.set()

        if not self._machine.is_closed:
            self._machine.transition(Event.CLOSE)

        # Wake anyone blocked in login() rather than making them wait out the
        # full timeout for a reply that is never coming.
        self._reply_ready.set()

        try:
            self._outbox.put_nowait(_STOP)
        except queue.Full:
            pass
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

    def __enter__(self) -> ServerConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ----------------------------------------------------------- handshake ---

    def register(self, username: str, pass_hash: str, timeout: float = REPLY_TIMEOUT) -> Frame:
        """Create an account. Returns the OK or ERROR frame the server sent."""
        self._require(ConnectionState.AUTHENTICATING, "register")
        return self._request(
            Frame(type=MessageType.REGISTER, data={"user": username, "pass_hash": pass_hash}),
            expecting={MessageType.OK, MessageType.ERROR},
            timeout=timeout,
        )

    def login(self, username: str, pass_hash: str, timeout: float = REPLY_TIMEOUT) -> Frame:
        """Log in, and move to ONLINE if the server agrees.

        Returns the LOGIN_OK frame -- which carries the roster and room list --
        or the ERROR frame explaining the refusal.
        """
        self._require(ConnectionState.AUTHENTICATING, "login")
        reply = self._request(
            Frame(type=MessageType.LOGIN, data={"user": username, "pass_hash": pass_hash}),
            expecting={MessageType.LOGIN_OK, MessageType.ERROR},
            timeout=timeout,
        )

        if reply.type is MessageType.LOGIN_OK:
            self.username = username
            self._machine.transition(Event.LOGIN_OK)
        else:
            self._machine.transition(Event.LOGIN_REFUSED)
        return reply

    # ------------------------------------------------------------- sending ---

    def send(self, frame: Frame) -> None:
        """Queue a frame for the server. Never blocks, never touches the socket."""
        if not self._machine.can_send:
            raise NotConnected(f"cannot send while {self._machine.state}")
        try:
            self._outbox.put_nowait(frame)
        except queue.Full:
            log.warning("outbox full, dropping the connection")
            self.close()
            raise NotConnected("the server is not keeping up; connection dropped") from None

    def message(self, to: str, body: str, nonce: str | None = None) -> Frame:
        """Send a chat message. `body` is plaintext until phase 6."""
        frame = Frame(type=MessageType.MSG, to=to, body=body, nonce=nonce)
        self.send(frame)
        return frame

    def ping(self) -> None:
        self.send(Frame(type=MessageType.PING))

    # -------------------------------------------------------------- private ---

    def _require(self, state: ConnectionState, action: str) -> None:
        if self._machine.state is not state:
            raise NotConnected(f"cannot {action} while {self._machine.state}")

    def _request(self, frame: Frame, expecting: set[MessageType], timeout: float) -> Frame:
        """Send one frame and block until a reply of an expected type arrives."""
        with self._await_lock:
            self._reply = None
            self._reply_ready.clear()
            self._await_types = frozenset(expecting)

        try:
            self._outbox.put_nowait(frame)
        except queue.Full:
            self._await_types = frozenset()
            raise NotConnected("outbox full during the handshake") from None

        if not self._reply_ready.wait(timeout):
            self._await_types = frozenset()
            raise TimeoutError(f"no reply to {frame.type} within {timeout}s")

        with self._await_lock:
            reply, self._reply = self._reply, None
            self._await_types = frozenset()

        if reply is None:
            # close() releases the event to unblock us; there is no reply.
            raise NotConnected("connection closed during the handshake")
        return reply

    def _deliver(self, frame: Frame) -> None:
        """Route one decoded frame: to a waiting request, or to the listener."""
        if frame.type in self._await_types:
            with self._await_lock:
                self._reply = frame
                self._await_types = frozenset()
            self._reply_ready.set()
            return

        if self._on_frame is not None:
            try:
                self._on_frame(frame)
            except Exception:
                # A broken listener must not kill the reader thread and take
                # the whole connection down with it.
                log.exception("listener raised on a %s frame", frame.type)

    def _read_loop(self) -> None:
        buffer = LineBuffer()
        try:
            while not self._closing.is_set():
                assert self._sock is not None
                try:
                    chunk = self._sock.recv(RECV_BYTES)
                except OSError:
                    break
                if not chunk:
                    break  # Server hung up.

                try:
                    lines = buffer.feed(chunk)
                except ProtocolError:
                    log.exception("the server sent something unreadable")
                    break

                for line in lines:
                    try:
                        frame = decode(line)
                    except ProtocolError:
                        log.exception("undecodable frame from the server, skipping it")
                        continue
                    self._deliver(frame)
        finally:
            self._on_connection_lost()

    def _write_loop(self) -> None:
        while True:
            item = self._outbox.get()
            if item is _STOP:
                return
            try:
                wire = encode(item)
            except Exception:
                # Skipping loses one frame; raising would kill this thread and
                # leave the connection open but permanently mute.
                log.exception("could not encode a frame, skipping it")
                continue
            try:
                assert self._sock is not None
                self._sock.sendall(wire)
            except OSError:
                return

    def _on_connection_lost(self) -> None:
        """The socket went away. Distinguish a deliberate close from a drop."""
        if self._closing.is_set() or self._machine.is_closed:
            return
        if self._machine.allows(Event.CONNECTION_LOST):
            self._machine.transition(Event.CONNECTION_LOST)
        # Unblock a handshake that will never be answered.
        self._reply_ready.set()
        log.info("connection lost, now %s", self._machine.state)
