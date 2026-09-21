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
from im.client.net.session import Session
from im.client.view.console import ConsoleView
from im.common.frames import MessageType
from im.crypto.identity import Identity
from im.crypto.keyring import Keyring
from im.crypto.tls import client_context

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
        help="which interface to run",
    )
    parser.add_argument(
        "--keyfile",
        default=None,
        help="where this user's private key lives (default: keys/<user>.key)",
    )
    parser.add_argument(
        "--plaintext",
        action="store_true",
        help="turn end-to-end encryption off, so the server can read message bodies",
    )
    parser.add_argument("--tls", action="store_true", help="connect over TLS")
    parser.add_argument(
        "--cacert", default="dev.crt", help="certificate to trust when using --tls"
    )
    parser.add_argument(
        "--server-hostname",
        default="localhost",
        help=(
            "the name the certificate should match, which is not always the address "
            "dialled -- a tunnel or a bare IP needs it stated separately"
        ),
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

    print(f"Semaphore -- client {__version__}")
    username = args.user or input("username: ").strip()
    password = args.password or getpass.getpass("password: ")
    digest = hash_password(password)

    keyring = None
    if not args.plaintext:
        # Reused if it exists. A new key would make every message anybody had
        # already sent to this user undecryptable.
        keyfile = args.keyfile or f"keys/{username}.key"
        keyring = Keyring(Identity.load_or_create(keyfile))

    model = ChatModel()
    # A Session rather than a bare connection: it owns the credentials, so it
    # can log in again by itself when the link drops. The controller cannot
    # tell the difference.
    session = Session(
        args.host,
        args.port,
        username,
        digest,
        pubkey=keyring.public_b64 if keyring else None,
        tls=client_context(args.cacert) if args.tls else None,
        server_hostname=args.server_hostname if args.tls else None,
    )
    controller = ChatController(session, model, keyring=keyring)

    # The only line that differs between the two interfaces. Everything
    # below this point is identical, which is the MVC claim made concrete.
    if args.view == "tk":
        from im.client.view.tk import TkView

        view = TkView(controller)
    else:
        view = ConsoleView(controller)

    # Every reconnection reuses these. Both only enqueue, so the model is
    # still touched by nothing but the view's own loop.
    session.on_frame = view.post_frame
    session.on_state = view.post_state

    try:
        reply = session.start(register=args.register)
    except ConnectionError as exc:
        print(f"  ! {exc}")
        return 1

    if reply.type is MessageType.ERROR:
        print(f"  ! {reply.data.get('message')}")
        return 1

    try:
        # Replay the handshake through the controller so the model learns who
        # we are and who else is here, exactly as it would from any frame.
        controller.on_frame(reply)
        controller.on_state(str(session.state))

        if isinstance(view, ConsoleView):
            view.read_stdin_forever()
        view.run()
    finally:
        session.close()

    print("\n  bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
