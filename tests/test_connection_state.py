"""Connection state machine tests. Phase 3.

No sockets. Which transitions are legal is a design decision, and checking it
should not need a network any more than the router's rules do.
"""

from __future__ import annotations

import pytest

from im.client.net.state import (
    ConnectionState,
    ConnectionStateMachine,
    Event,
    IllegalTransition,
)


@pytest.fixture
def machine() -> ConnectionStateMachine:
    return ConnectionStateMachine()


def drive(machine: ConnectionStateMachine, *events: Event) -> ConnectionState:
    for event in events:
        machine.transition(event)
    return machine.state


def test_a_connection_starts_disconnected(machine: ConnectionStateMachine) -> None:
    assert machine.state is ConnectionState.DISCONNECTED
    assert not machine.can_send


def test_the_happy_path_reaches_online(machine: ConnectionStateMachine) -> None:
    assert (
        drive(machine, Event.CONNECT, Event.SOCKET_READY, Event.LOGIN_OK) is ConnectionState.ONLINE
    )
    assert machine.can_send


def test_a_failed_socket_goes_to_retrying(machine: ConnectionStateMachine) -> None:
    assert drive(machine, Event.CONNECT, Event.SOCKET_FAILED) is ConnectionState.RETRYING
    assert not machine.can_send


def test_backoff_retries_the_connection(machine: ConnectionStateMachine) -> None:
    drive(machine, Event.CONNECT, Event.SOCKET_FAILED)
    assert machine.transition(Event.BACKOFF_EXPIRED) is ConnectionState.CONNECTING


def test_retrying_can_fail_again_and_stay_retrying(machine: ConnectionStateMachine) -> None:
    """Backoff has to survive a server that is down for a while."""
    drive(machine, Event.CONNECT, Event.SOCKET_FAILED)
    for _ in range(3):
        assert machine.transition(Event.SOCKET_FAILED) is ConnectionState.RETRYING


def test_a_refused_login_waits_for_the_user_rather_than_retrying(
    machine: ConnectionStateMachine,
) -> None:
    """A wrong password is not a network problem: retrying it would just fail
    again, and lock the account out on a server that counted attempts."""
    drive(machine, Event.CONNECT, Event.SOCKET_READY)
    assert machine.transition(Event.LOGIN_REFUSED) is ConnectionState.DISCONNECTED


def test_losing_the_connection_while_online_goes_to_retrying(
    machine: ConnectionStateMachine,
) -> None:
    drive(machine, Event.CONNECT, Event.SOCKET_READY, Event.LOGIN_OK)
    assert machine.transition(Event.CONNECTION_LOST) is ConnectionState.RETRYING


def test_losing_the_connection_mid_handshake_also_retries(
    machine: ConnectionStateMachine,
) -> None:
    drive(machine, Event.CONNECT, Event.SOCKET_READY)
    assert machine.transition(Event.CONNECTION_LOST) is ConnectionState.RETRYING


@pytest.mark.parametrize(
    "before",
    [
        (),
        (Event.CONNECT,),
        (Event.CONNECT, Event.SOCKET_READY),
        (Event.CONNECT, Event.SOCKET_READY, Event.LOGIN_OK),
        (Event.CONNECT, Event.SOCKET_FAILED),
    ],
)
def test_close_is_legal_from_anywhere(
    machine: ConnectionStateMachine, before: tuple[Event, ...]
) -> None:
    """The user may quit at any moment, including halfway through a handshake."""
    drive(machine, *before)
    assert machine.transition(Event.CLOSE) is ConnectionState.CLOSED
    assert machine.is_closed
    assert not machine.can_send


def test_closed_is_terminal(machine: ConnectionStateMachine) -> None:
    machine.transition(Event.CLOSE)
    with pytest.raises(IllegalTransition, match="after the connection was closed"):
        machine.transition(Event.CONNECT)


def test_an_illegal_event_raises_rather_than_being_ignored(
    machine: ConnectionStateMachine,
) -> None:
    """Silently staying put would turn a logic error into a connection that
    looks fine and quietly does nothing."""
    with pytest.raises(IllegalTransition, match="LOGIN_OK is not legal in DISCONNECTED"):
        machine.transition(Event.LOGIN_OK)


def test_you_cannot_send_before_logging_in(machine: ConnectionStateMachine) -> None:
    for events in ((), (Event.CONNECT,), (Event.CONNECT, Event.SOCKET_READY)):
        fresh = ConnectionStateMachine()
        drive(fresh, *events)
        assert not fresh.can_send


def test_allows_reports_without_applying(machine: ConnectionStateMachine) -> None:
    assert machine.allows(Event.CONNECT)
    assert not machine.allows(Event.LOGIN_OK)
    assert machine.state is ConnectionState.DISCONNECTED  # unchanged


def test_every_change_notifies_the_observer() -> None:
    """How a view greys out its composer without polling."""
    seen: list[ConnectionState] = []
    machine = ConnectionStateMachine(on_change=seen.append)

    drive(machine, Event.CONNECT, Event.SOCKET_READY, Event.LOGIN_OK, Event.CONNECTION_LOST)

    assert seen == [
        ConnectionState.CONNECTING,
        ConnectionState.AUTHENTICATING,
        ConnectionState.ONLINE,
        ConnectionState.RETRYING,
    ]


def test_a_transition_that_changes_nothing_does_not_notify() -> None:
    seen: list[ConnectionState] = []
    machine = ConnectionStateMachine(on_change=seen.append)
    drive(machine, Event.CONNECT, Event.SOCKET_FAILED)
    seen.clear()

    machine.transition(Event.SOCKET_FAILED)  # RETRYING -> RETRYING

    assert seen == []
