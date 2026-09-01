"""Entry point for the hub server: ``python -m im.server``.

Phase 0 -- this starts, parses arguments and prints a banner. The accept loop
arrives in phase 1.
"""

from __future__ import annotations

import argparse

from im import __version__

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
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="port to listen on"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"Socket to Ciphertext -- server {__version__}")
    print(f"  listen   {args.host}:{args.port}")
    print("  phase    0 (skeleton -- the accept loop lands in phase 1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
