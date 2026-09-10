"""Accounts, on disk, with salted and derived password digests.

What arrives from the client is already a digest -- the protocol carries
`pass_hash`, never a password -- but storing that as-is would be no better
than storing the password: identical passwords would produce identical rows,
and a stolen database would be a rainbow-table lookup away from every account.

So the server derives again, with a per-user random salt and scrypt. Two
users who chose the same password now have nothing in common on disk, and
guessing costs real time and memory per attempt rather than one hash.

Phase 2 kept these in memory; nothing above this module noticed the change.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time

from im.server.store.db import Database

#: scrypt cost. n is the work factor: raising it by one doubling doubles both
#: the time and the memory an attacker needs per guess. 2**14 costs roughly a
#: tenth of a second here, which nobody notices once per login and which makes
#: bulk guessing expensive.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32


class SqliteUsers:
    """Username to salted, derived digest.

    Registration is a compound check-then-write, so it happens inside one
    transaction: two clients registering the same name at the same moment
    must not both succeed.
    """

    def __init__(self, database: Database, scrypt_n: int = SCRYPT_N) -> None:
        self.db = database
        # Overridable so the tests can run hundreds of logins without paying
        # the real cost. Never lower it in anything a person logs in to.
        self._n = scrypt_n
        self._lock = threading.Lock()

    def _derive(self, pass_hash: str, salt: bytes) -> bytes:
        import hashlib

        return hashlib.scrypt(
            pass_hash.encode("utf-8"),
            salt=salt,
            n=self._n,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=KEY_BYTES,
        )

    def register(self, username: str, pass_hash: str, pubkey: str | None = None) -> bool:
        """Create an account. False if the name is taken."""
        salt = secrets.token_bytes(SALT_BYTES)
        digest = self._derive(pass_hash, salt)

        with self.db.write() as conn:
            taken = conn.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            ).fetchone()
            if taken is not None:
                return False
            conn.execute(
                "INSERT INTO users (username, salt, digest, pubkey, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (username, salt, digest, pubkey, int(time.time() * 1000)),
            )
        return True

    def verify(self, username: str, pass_hash: str) -> bool:
        """Check a login. False for both a wrong password and an unknown user."""
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT salt, digest FROM users WHERE username = ?", (username,)
            ).fetchone()

        if row is None:
            # Derive against a throwaway salt anyway, so an unknown username
            # costs the same time as a wrong password. Skipping the work here
            # would let anyone find out which accounts exist by timing.
            self._derive(pass_hash, secrets.token_bytes(SALT_BYTES))
            return False

        # Constant time: a plain == returns early on the first differing byte,
        # leaking the digest one byte at a time to anyone willing to measure.
        return hmac.compare_digest(row["digest"], self._derive(pass_hash, row["salt"]))

    def exists(self, username: str) -> bool:
        with self.db.read() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM users WHERE username = ?", (username,)
                ).fetchone()
                is not None
            )

    def set_pubkey(self, username: str, pubkey: str) -> None:
        """Store a public key at registration. Used from phase 6."""
        with self.db.write() as conn:
            conn.execute("UPDATE users SET pubkey = ? WHERE username = ?", (pubkey, username))

    def pubkey(self, username: str) -> str | None:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT pubkey FROM users WHERE username = ?", (username,)
            ).fetchone()
        return None if row is None else row["pubkey"]

    def __len__(self) -> int:
        with self.db.read() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])
