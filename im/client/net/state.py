"""The client's connection state machine -- figure 4 of the design.

                  +----------------+
       start ---->|  DISCONNECTED  |<-------- login refused
                  +--------+-------+
                       connect()
                           v
                  +----------------+  socket fails   +------------+
                  |   CONNECTING   |---------------->|  RETRYING  |
                  +--------+-------+                 +-----+------+
                     socket ready                   backoff expires
                           v                               |
                  +----------------+  LOGIN_OK             |
                  | AUTHENTICATING |-------+               |
                  +--------+-------+       |               |
             login refused |               v               |
                           +------> +--------------+       |
                                    |    ONLINE    |<------+
                                    +------+-------+  connection lost
                                       close()
                                           v
                                    +--------------+
                                    |    CLOSED    |
                                    +--------------+

Kept apart from the socket code on purpose. Which states exist and which
transitions are legal is a design decision that a view has to render and a
test has to check, and neither should need a network to do it.

A view renders `state` and nothing else: the composer is enabled only while
`can_send` is true, and RETRYING is where a countdown belongs.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum


class ConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATING = "AUTHENTICATING"
    ONLINE = "ONLINE"
    RETRYING = "RETRYING"
    CLOSED = "CLOSED"


class Event(StrEnum):
    """What can happen to a connection. The arrows in the diagram above."""

    CONNECT = "CONNECT"
    SOCKET_READY = "SOCKET_READY"
    SOCKET_FAILED = "SOCKET_FAILED"
    LOGIN_OK = "LOGIN_OK"
    LOGIN_REFUSED = "LOGIN_REFUSED"
    CONNECTION_LOST = "CONNECTION_LOST"
    BACKOFF_EXPIRED = "BACKOFF_EXPIRED"
    CLOSE = "CLOSE"


class IllegalTransition(Exception):
    """An event that makes no sense in the current state.

    Raised rather than ignored: silently staying put would turn a logic error
    into a connection that looks fine and does nothing.
    """


#: Every legal (state, event) pair. Anything absent is a bug in the caller.
#: CLOSE is handled separately -- it is legal from anywhere.
_TRANSITIONS: dict[tuple[ConnectionState, Event], ConnectionState] = {
    (ConnectionState.DISCONNECTED, Event.CONNECT): ConnectionState.CONNECTING,
    (ConnectionState.CONNECTING, Event.SOCKET_READY): ConnectionState.AUTHENTICATING,
    (ConnectionState.CONNECTING, Event.SOCKET_FAILED): ConnectionState.RETRYING,
    (ConnectionState.AUTHENTICATING, Event.LOGIN_OK): ConnectionState.ONLINE,
    # A refusal is not a network problem, so it goes back to DISCONNECTED and
    # waits for the user rather than retrying a password that will not work.
    (ConnectionState.AUTHENTICATING, Event.LOGIN_REFUSED): ConnectionState.DISCONNECTED,
    (ConnectionState.AUTHENTICATING, Event.CONNECTION_LOST): ConnectionState.RETRYING,
    (ConnectionState.ONLINE, Event.CONNECTION_LOST): ConnectionState.RETRYING,
    (ConnectionState.RETRYING, Event.BACKOFF_EXPIRED): ConnectionState.CONNECTING,
    (ConnectionState.RETRYING, Event.SOCKET_FAILED): ConnectionState.RETRYING,
}

#: Once here, nothing else happens. close() is final.
_TERMINAL = ConnectionState.CLOSED


class ConnectionStateMachine:
    """One state, one transition() method, and an optional observer.

    The observer is called with the new state after every change, which is how
    a view learns to grey out its composer without polling.
    """

    def __init__(self, on_change: Callable[[ConnectionState], None] | None = None) -> None:
        self._state = ConnectionState.DISCONNECTED
        self._on_change = on_change

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def can_send(self) -> bool:
        """Whether the user may type. True in exactly one state."""
        return self._state is ConnectionState.ONLINE

    @property
    def is_closed(self) -> bool:
        return self._state is _TERMINAL

    def allows(self, event: Event) -> bool:
        """Whether `event` is legal right now, without applying it."""
        if self._state is _TERMINAL:
            return False
        if event is Event.CLOSE:
            return True
        return (self._state, event) in _TRANSITIONS

    def transition(self, event: Event) -> ConnectionState:
        """Apply an event. Returns the new state, or raises IllegalTransition."""
        if self._state is _TERMINAL:
            raise IllegalTransition(f"{event} after the connection was closed")

        if event is Event.CLOSE:
            # Legal from anywhere: the user may quit at any moment, including
            # halfway through a handshake.
            new_state = _TERMINAL
        else:
            try:
                new_state = _TRANSITIONS[(self._state, event)]
            except KeyError:
                raise IllegalTransition(f"{event} is not legal in {self._state}") from None

        if new_state is not self._state:
            self._state = new_state
            if self._on_change is not None:
                self._on_change(new_state)
        return new_state
