"""A client's long-term X25519 identity key.

The private half never leaves the machine that generated it. The public half
is handed to the server at registration and given out to anyone who asks for
it with GET_KEY, which is how two clients agree a shared secret without ever
sending one.

Nothing here implements a cipher. Every primitive comes from the
`cryptography` library, which is the only responsible way to do this.
"""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


class Identity:
    """One user's keypair."""

    def __init__(self, private: X25519PrivateKey) -> None:
        self._private = private

    @classmethod
    def generate(cls) -> Identity:
        return cls(X25519PrivateKey.generate())

    # ------------------------------------------------------------- the keys ---

    @property
    def public_bytes(self) -> bytes:
        return self._private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    @property
    def public_b64(self) -> str:
        """What goes to the server, and into a GET_KEY reply."""
        return b64(self.public_bytes)

    def exchange(self, peer_public_b64: str) -> bytes:
        """The raw shared secret with one other identity.

        Both sides compute the same 32 bytes from their own private key and
        the other's public key, having sent nothing secret to each other.
        Never used as a key directly -- see envelope.derive_key.
        """
        peer = X25519PublicKey.from_public_bytes(unb64(peer_public_b64))
        return self._private.exchange(peer)

    # ------------------------------------------------------------- on disk ---

    def save(self, path: str | Path) -> None:
        """Write the private key out.

        Deliberately unencrypted, and the threat model says so: anyone with
        the unlocked machine can read it. Protecting it would need a
        passphrase the user types at every start, which is a design decision
        this project does not make.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            self._private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        )
        try:
            path.chmod(0o600)  # No effect on Windows, correct everywhere else.
        except OSError:
            pass

    @classmethod
    def load(cls, path: str | Path) -> Identity:
        return cls(X25519PrivateKey.from_private_bytes(Path(path).read_bytes()))

    @classmethod
    def load_or_create(cls, path: str | Path) -> Identity:
        """The normal path: reuse the key if there is one, else make it.

        Generating a new key when one already exists would silently make every
        message anybody previously sent to this user undecryptable.
        """
        path = Path(path)
        if path.exists():
            return cls.load(path)
        identity = cls.generate()
        identity.save(path)
        return identity
