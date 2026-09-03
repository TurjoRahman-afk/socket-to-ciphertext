"""Command line entry points.

The server's main() now blocks in the accept loop, so serving is covered by
tests/test_echo_server.py against real sockets. What is checked here is the
wiring around it: arguments parsed, banner printed, shutdown always run.
"""

from __future__ import annotations

import im.client.__main__ as client_main
import im.server.__main__ as server_main
from im.server.server import ChatServer


def test_server_parses_host_and_port() -> None:
    args = server_main.parse_args(["--host", "0.0.0.0", "--port", "5050"])
    assert (args.host, args.port, args.quiet) == ("0.0.0.0", 5050, False)


def test_client_parses_host_port_and_view() -> None:
    args = client_main.parse_args(["--host", "10.0.0.4", "--port", "5050", "--view", "tk"])
    assert (args.host, args.port, args.view) == ("10.0.0.4", 5050, "tk")


def test_server_binds_prints_a_banner_and_shuts_down(monkeypatch, capsys) -> None:
    """Port 0 so the test never collides with a server the user is running.
    serve_forever is stubbed out -- the accept loop is tested elsewhere, and
    left real it would block here forever."""
    served = []
    monkeypatch.setattr(ChatServer, "serve_forever", lambda self: served.append(True))
    shutdowns = []
    original_shutdown = ChatServer.shutdown
    monkeypatch.setattr(
        ChatServer,
        "shutdown",
        lambda self: (shutdowns.append(True), original_shutdown(self))[1],
    )

    assert server_main.main(["--port", "0", "--quiet"]) == 0

    out = capsys.readouterr().out
    assert "Socket to Ciphertext" in out
    assert "telnet" in out
    assert served == [True]
    assert shutdowns == [True], "shutdown must run even on the happy path"


def test_server_shuts_down_on_keyboard_interrupt(monkeypatch) -> None:
    """Ctrl-C is the documented way to stop it, so it must close the socket
    rather than leaving the port held."""

    def interrupt(self: ChatServer) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(ChatServer, "serve_forever", interrupt)
    shutdowns = []
    original_shutdown = ChatServer.shutdown
    monkeypatch.setattr(
        ChatServer,
        "shutdown",
        lambda self: (shutdowns.append(True), original_shutdown(self))[1],
    )

    assert server_main.main(["--port", "0", "--quiet"]) == 0
    assert shutdowns == [True]


def test_the_tk_view_says_it_is_not_here_yet(capsys) -> None:
    """Chosen as the client's entry-point test because it is the one path
    that returns without prompting for credentials or opening a socket."""
    assert client_main.main(["--view", "tk"]) == 1
    assert "phase 7" in capsys.readouterr().out


def test_a_password_never_leaves_the_client_in_the_clear() -> None:
    digest = client_main.hash_password("hunter2")
    assert "hunter2" not in digest
    assert len(digest) == 64  # sha256, hex
    assert digest == client_main.hash_password("hunter2")  # stable
    assert digest != client_main.hash_password("hunter3")
