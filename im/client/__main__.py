"""Entry point for the client: ``python -m im.client``.

Phase 0 -- this starts, parses arguments and prints a banner. The console view
arrives in phase 3; the Tkinter view in phase 7.
"""

from __future__ import annotations

import argparse

from im import __version__

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m im.client",
        description="Socket to Ciphertext -- the client.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="server address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="server port")
    parser.add_argument(
        "--view",
        choices=("console", "tk"),
        default="console",
        help="which view to run (tk arrives in phase 7)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"Socket to Ciphertext -- client {__version__}")
    print(f"  server   {args.host}:{args.port}")
    print(f"  view     {args.view}")
    print("  state    DISCONNECTED")
    print("  phase    0 (skeleton -- the connection lands in phase 3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
