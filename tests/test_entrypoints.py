"""Phase 0 exit criteria: both entry points start and print a banner."""

from __future__ import annotations

import im.client.__main__ as client_main
import im.server.__main__ as server_main


def test_server_starts_and_prints_a_banner(capsys) -> None:
    assert server_main.main([]) == 0
    assert "Socket to Ciphertext" in capsys.readouterr().out


def test_client_starts_and_prints_a_banner(capsys) -> None:
    assert client_main.main([]) == 0
    assert "Socket to Ciphertext" in capsys.readouterr().out


def test_server_accepts_host_and_port() -> None:
    args = server_main.parse_args(["--host", "0.0.0.0", "--port", "5050"])
    assert (args.host, args.port) == ("0.0.0.0", 5050)


def test_client_accepts_host_port_and_view() -> None:
    args = client_main.parse_args(["--host", "10.0.0.4", "--port", "5050", "--view", "tk"])
    assert (args.host, args.port, args.view) == ("10.0.0.4", 5050, "tk")
