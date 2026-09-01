"""Command line entry points.

The server's main() now blocks in the accept loop, so serving is covered by
tests/test_echo_server.py against real sockets. What is checked here is the
wiring around it: arguments parsed, banner printed, shutdown always run.
"""

from __future__ import annotations

import im.client.__main__ as client_main
import im.server.__main__ as server_main
from im.server.server import EchoServer


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
    monkeypatch.setattr(EchoServer, "serve_forever", lambda self: served.append(True))
    shutdowns = []
    original_shutdown = EchoServer.shutdown
    monkeypatch.setattr(
        EchoServer,
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

    def interrupt(self: EchoServer) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(EchoServer, "serve_forever", interrupt)
    shutdowns = []
    original_shutdown = EchoServer.shutdown
    monkeypatch.setattr(
        EchoServer,
        "shutdown",
        lambda self: (shutdowns.append(True), original_shutdown(self))[1],
    )

    assert server_main.main(["--port", "0", "--quiet"]) == 0
    assert shutdowns == [True]


def test_client_starts_and_prints_a_banner(capsys) -> None:
    assert client_main.main([]) == 0
    assert "Socket to Ciphertext" in capsys.readouterr().out
