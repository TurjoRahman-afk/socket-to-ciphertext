"""Entry point for the client: ``python -m im.client``.

Phase 3 -- wires the connection, the model, the controller and the console
view together, and runs them. The Tk view arrives in phase 7 and will replace
only the last of those four.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import logging

from im import __version__
from im.client.controller.chat import ChatController
from im.client.model.chat import ChatModel
from im.client.net.connection import ServerConnection
from im.client.view.console import ConsoleView
from im.common.frames import MessageType

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000


def hash_password(password: str) -> str:
    """Turn a typed password into the digest the protocol carries.

    A plain SHA-256 for now, so a password never travels or rests in the
    clear. It is *not* yet a password KDF: there is no salt and no work
    factor, so two people with the same password produce the same digest.
    Phase 5 replaces this with a per-user salt and hashlib.scrypt.
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m im.client",
        description="Socket to Ciphertext -- the client.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="server address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="server port")
    parser.add_argument("--user", help="username (prompted for if omitted)")
    parser.add_argument(
        "--password",
        help="password (prompted for if omitted; passing it here puts it in your shell history)",
    )
    parser.add_argument(
        "--register",
        action="store_true",
        help="create the account first, then log in",
    )
    parser.add_argument(
        "--view",
        choices=("console", "tk"),
        default="console",
        help="which view to run (tk arrives in phase 7)",
    )
    parser.add_argument("--quiet", action="store_true", help="log warnings and errors only")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.view == "tk":
        print("The Tkinter view arrives in phase 7. Use --view console for now.")
        return 1

    print(f"Socket to Ciphertext -- client {__version__}")
    username = args.user or input("username: ").strip()
    password = args.password or getpass.getpass("password: ")
    digest = hash_password(password)

    model = ChatModel()
    connection = ServerConnection(args.host, args.port)
    controller = ChatController(connection, model)
    view = ConsoleView(controller)

    # The connection calls these on its reader thread; both only enqueue, so
    # the model is still only ever touched by the view's own loop.
    connection.listen(on_frame=view.post_frame, on_state=view.post_state)

    try:
        connection.connect()
    except ConnectionError as exc:
        print(f"  ! {exc}")
        return 1

    try:
        if args.register:
            reply = connection.register(username, digest)
            if reply.type is MessageType.ERROR:
                print(f"  ! could not register: {reply.data.get('message')}")
                return 1
            print(f"  registered {username}")

        reply = connection.login(username, digest)
        if reply.type is MessageType.ERROR:
            print(f"  ! could not log in: {reply.data.get('message')}")
            return 1

        # Replay the handshake through the controller so the model learns who
        # we are and who else is here, exactly as it would from any frame.
        controller.on_frame(reply)
        controller.on_state(str(connection.state))

        view.read_stdin_forever()
        view.run()
    finally:
        connection.close()

    print("\n  bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
