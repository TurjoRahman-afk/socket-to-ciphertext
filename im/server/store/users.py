"""Accounts. Phase 2 keeps them in memory; phase 5 moves them into sqlite.

A plaintext password never reaches this module. The client hashes it first and
sends the digest as the protocol's `pass_hash` field, and what is stored here
is that digest. Note honestly what this is not: there is no per-user salt and
no key derivation function yet, so identical passwords still produce identical
digests. Phase 5 replaces this with a salt and hashlib.scrypt.
"""

# it's a small in memory user/account database
from __future__ import annotations

import hmac
import threading


class InMemoryUsers:
    """Username to password digest, guarded by a lock.

    Registration is a compound check-then-write, so it happens inside one
    critical section: two clients registering the same name at the same
    moment must not both succeed.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()  # protects the dictionary
        self._digests: dict[str, str] = {}  # stores username

    def register(self, username: str, pass_hash: str) -> bool:
        """Create an account. False if the name is taken."""
        with self._lock:
            if username in self._digests:
                return False
            self._digests[username] = pass_hash
            return True

    def verify(self, username: str, pass_hash: str) -> bool:
        """Check a login. False for both a wrong password and an unknown user."""
        with self._lock:
            stored = self._digests.get(username)

        if stored is None:
            # Compare anyway, so an unknown username costs the same time as a
            # wrong password. Otherwise the difference tells an attacker which
            # accounts exist before they even guess a password.
            hmac.compare_digest(pass_hash, pass_hash)
            return False

        # Constant time: a plain == would return early on the first differing
        # character, leaking the digest one character at a time to anyone
        # willing to measure.
        return hmac.compare_digest(stored, pass_hash)

    def exists(self, username: str) -> bool:
        with self._lock:
            return username in self._digests

    def __len__(self) -> int:
        with self._lock:
            return len(self._digests)
