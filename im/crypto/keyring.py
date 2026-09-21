"""The keys a client holds: its own identity, and everyone else's public half.

A public key has to be fetched before the first message to someone, so this
also remembers which people are still unknown. Derived message keys are cached
because the X25519 exchange and the HKDF step are pure functions of the two
keys -- redoing them per message would be work for nothing.

Rooms are encrypted too, by sealing the body once per member. A frame carries
one body, so the per-member ciphertexts travel in `data["env"]` and the server
hands each member only their own. That is wasteful at scale -- N ciphertexts
for N members -- and entirely reasonable for a room of five. It is the honest
version of the alternative, which was to leave rooms in plaintext and say so.

A copy is sealed for the sender as well. X25519 with one's own key is a
perfectly good exchange, and without it a sender could not read their own room
messages back from history after restarting.
"""

from __future__ import annotations

from im.crypto.envelope import DecryptionFailed, key_for, open_, seal
from im.crypto.identity import Identity

ROOM_PREFIX = "#"


class Keyring:
    def __init__(self, identity: Identity, me: str = "") -> None:
        self.identity = identity
        # Our own name, so a copy can be sealed to ourselves. Set at login if
        # it was not known at construction.
        self._me = me
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
        if user and user == self._me:
            self._self_key()
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

        Everything can. Kept as a method because the controller asks, and
        because it once answered False for rooms.
        """
        return True

    def _self_key(self) -> None:
        """Make our own public key available under our own name."""
        # Sealing to yourself needs your own public half in the same place
        # everyone else's lives, so _key() finds it without a special case.
        self._pubkeys.setdefault(self._me, self.public_b64)

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

    def seal_for_members(
        self, members: list[str], plaintext: str, sender: str
    ) -> dict[str, list[str]] | None:
        """Seal one room message once per member.

        Returns {member: [ciphertext, nonce]}, or None if any member's key is
        missing -- all or nothing, because sending to the members we happen to
        have keys for would quietly drop the rest out of the conversation with
        no sign that it had happened.

        The sender is included, so their own history is readable later.
        """
        self._me = sender
        self._self_key()

        everyone = sorted(set(members) | {sender})
        if any(self._key(name) is None for name in everyone):
            return None

        # A fresh nonce per member as well as per message. Two members must
        # never share a nonce, because they do not share a key either and the
        # pairing is what makes reuse catastrophic.
        return {name: list(self.seal(name, plaintext, sender)) for name in everyone}

    def missing_keys(self, members: list[str]) -> list[str]:
        """Which of these people we cannot encrypt to yet."""
        return sorted(name for name in set(members) if not self.knows(name))

    def open(self, user: str, ciphertext: str, nonce: str, sender: str) -> str:
        """Decrypt from one person. Raises DecryptionFailed."""
        key = self._key(user)
        if key is None:
            raise DecryptionFailed(f"no key held for {user}")
        return open_(key, ciphertext, nonce, associated=sender)
