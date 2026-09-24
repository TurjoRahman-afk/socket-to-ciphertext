"""Command line entry points.

The server's main() now blocks in the accept loop, so serving is covered by
tests/test_echo_server.py against real sockets. What is checked here is the
wiring around it: arguments parsed, banner printed, shutdown always run.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

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


def test_the_client_offers_both_interfaces() -> None:
    """One flag chooses between them; everything below the view is identical."""
    assert client_main.parse_args(["--view", "tk"]).view == "tk"
    assert client_main.parse_args([]).view == "console"


def test_encryption_is_on_unless_it_is_turned_off() -> None:
    """A client that quietly fell back to plaintext would break the promise
    the whole of phase 6 exists to make."""
    assert client_main.parse_args([]).plaintext is False
    assert client_main.parse_args(["--plaintext"]).plaintext is True


def test_a_password_never_leaves_the_client_in_the_clear() -> None:
    digest = client_main.hash_password("hunter2")
    assert "hunter2" not in digest
    assert len(digest) == 64  # sha256, hex
    assert digest == client_main.hash_password("hunter2")  # stable
    assert digest != client_main.hash_password("hunter3")


def test_the_doctor_runs_and_reports() -> None:
    """The diagnostic is what somebody runs when nothing else works, so it
    must not be the thing that is broken."""
    result = subprocess.run(
        [sys.executable, "-m", "tools.doctor"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert "why will the window not open" in result.stdout
    assert "tkinter is importable" in result.stdout
    # 0 when the machine is healthy, 1 when it found something. Either is a
    # working diagnostic; a crash is not.
    assert result.returncode in (0, 1)


def test_asking_for_a_window_without_tkinter_explains_itself() -> None:
    """A Python built without Tcl/Tk runs the console view perfectly and
    cannot open a window. The message should say that, not show a traceback
    about tkinter to somebody who never mentioned tkinter."""
    with tempfile.TemporaryDirectory() as tmp:
        pathlib.Path(tmp, "tkinter.py").write_text(
            "raise ImportError('No module named _tkinter')\n", encoding="utf-8"
        )
        result = subprocess.run(
            [sys.executable, "-m", "im.client", "--view", "tk",
             "--user", "aya", "--password", "demo"],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONPATH": tmp},
        )

    output = result.stdout + result.stderr
    assert "The window cannot open on this machine" in output
    assert "--view console" in output, "it should point at the view that does work"
    assert "tools.doctor" in output
    assert "Traceback" not in output, "a traceback is not an explanation"
