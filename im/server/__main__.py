"""Entry point for the hub server: ``python -m im.server``.

Phase 1 -- runs the echo server. Connect two telnet sessions to it and every
line you type comes back.
"""

from __future__ import annotations

import argparse
import logging

from im import __version__
from im.server.server import ChatServer

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m im.server",
        description="Socket to Ciphertext -- the hub server.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="interface to bind (use 0.0.0.0 to accept connections from other machines)",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to listen on")
    parser.add_argument("--quiet", action="store_true", help="log warnings and errors only")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(threadName)-22s %(message)s",
        datefmt="%H:%M:%S",
    )

    server = ChatServer(args.host, args.port)
    host, port = server.bind()
    print(f"Socket to Ciphertext -- server {__version__}")
    print(f"  listen   {host}:{port}")
    print("  phase    2 (routing: LOGIN, MSG, PRESENCE)")
    print(f"  try      telnet {host} {port}")
    print("  stop     Ctrl-C")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()  # move off the ^C
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
