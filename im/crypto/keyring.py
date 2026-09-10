"""The keys a client holds: its own identity, and everyone else's public half.

A public key has to be fetched before the first message to someone, so this
also remembers which people are still unknown. Derived message keys are cached
because the X25519 exchange and the HKDF step are pure functions of the two
keys -- redoing them per message would be work for nothing.

Rooms are deliberately not encrypted yet, and the reason is structural rather
than an oversight: a frame carries one body, so a room message would need one
ciphertext per member inside it. That is written up as a known limitation
rather than quietly claimed as working.
"""

from __future__ import annotations

from im.crypto.envelope import DecryptionFailed, key_for, open_, seal
from im.crypto.identity import Identity

ROOM_PREFIX = "#"


class Keyring:
    def __init__(self, identity: Identity) -> None:
        self.identity = identity
        self._pubkeys: dict[str, str] = {}
        self._derived: dict[str, bytes] = {}

    @property
    def public_b64(self) -> str:
        """Our own public key, as published at registration."""
        return self.identity.public_b64

    # ----------------------------------------------------------- other people ---

    def remember(self, user: str, pubkey: str) -> None:
        """Store a public key that arrived in a KEY frame."""
        if self._pubkeys.get(user) == pubkey:
            return
        self._pubkeys[user] = pubkey
        # A changed key invalidates the derived one. It also means the server
        # may have substituted a key -- which this design cannot detect, and
        # which the threat model says so plainly.
        self._derived.pop(user, None)

    def knows(self, user: str) -> bool:
        return user in self._pubkeys

    def forget(self, user: str) -> None:
        self._pubkeys.pop(user, None)
        self._derived.pop(user, None)

    def _key(self, user: str) -> bytes | None:
        if user in self._derived:
            return self._derived[user]
        pubkey = self._pubkeys.get(user)
        if pubkey is None:
            return None
        derived = key_for(self.identity, pubkey)
        self._derived[user] = derived
        return derived

    # -------------------------------------------------------------- messages ---

    @staticmethod
    def encryptable(target: str) -> bool:
        """Whether a conversation can be encrypted at all.

        Rooms cannot yet -- see the module docstring.
        """
        return not target.startswith(ROOM_PREFIX)

    def seal(self, user: str, plaintext: str, sender: str) -> tuple[str, str] | None:
        """Encrypt for one person, or None if their key is not held yet.

        The sender's name is authenticated alongside the ciphertext, so the
        server cannot relabel a message as coming from somebody else without
        the recipient's decryption failing.
        """
        key = self._key(user)
        if key is None:
            return None
        return seal(key, plaintext, associated=sender)

    def open(self, user: str, ciphertext: str, nonce: str, sender: str) -> str:
        """Decrypt from one person. Raises DecryptionFailed."""
        key = self._key(user)
        if key is None:
            raise DecryptionFailed(f"no key held for {user}")
        return open_(key, ciphertext, nonce, associated=sender)
