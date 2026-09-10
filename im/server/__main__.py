"""Entry point for the hub server: ``python -m im.server``.

Phase 1 -- runs the echo server. Connect two telnet sessions to it and every
line you type comes back.
"""

from __future__ import annotations

import argparse
import logging

from im import __version__
from im.crypto.tls import generate_self_signed, server_context
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
    parser.add_argument(
        "--db",
        default="im.db",
        help="database file; pass :memory: for a server that forgets everything on exit",
    )
    parser.add_argument(
        "--tls",
        action="store_true",
        help="wrap connections in TLS, generating a development certificate if needed",
    )
    parser.add_argument("--cert", default="dev.crt", help="TLS certificate file")
    parser.add_argument("--key", default="dev.key", help="TLS private key file")
    parser.add_argument("--quiet", action="store_true", help="log warnings and errors only")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(threadName)-22s %(message)s",
        datefmt="%H:%M:%S",
    )

    context = None
    if args.tls:
        cert, key = generate_self_signed(args.cert, args.key)
        context = server_context(cert, key)

    server = ChatServer(args.host, args.port, db_path=args.db, tls=context)
    host, port = server.bind()
    print(f"Socket to Ciphertext -- server {__version__}")
    print(f"  listen   {host}:{port}")
    print(f"  store    {args.db}")
    print(f"  tls      {args.cert if args.tls else 'off (plaintext)'}")
    print("  phase    6 (TLS available, end-to-end encryption in the client)")
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
