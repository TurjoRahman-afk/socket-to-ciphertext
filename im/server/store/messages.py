"""Message history, and the queue for people who are not here right now.

Two jobs that share a table. Every message is recorded once. A message whose
recipient has no live connection additionally gets a row in `pending`, naming
who still owes a delivery -- so a room message to five people, two of them
offline, is stored once and queued twice rather than copied five times.

The server stores ciphertext from phase 6 onward. Nothing in this module
looks at `body`, which is what makes that a change of one layer rather than
a change here.
"""

from __future__ import annotations

from typing import Any

from im.server.store.db import Database

#: Separates the two names in a direct conversation's key. A unit separator
#: rather than a printable character, so it cannot occur in a username and
#: create a second conversation that looks like an existing one.
PAIR_SEPARATOR = "\x1f"

#: The three states a message can be in, from the sender's point of view.
SENT = "SENT"
DELIVERED = "DELIVERED"
READ = "READ"

#: How many messages a HISTORY request returns when it does not say.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def direct_conversation(a: str, b: str) -> str:
    """The canonical key for a conversation between two people.

    Sorted, so that alice-to-bob and bob-to-alice are the same conversation
    rather than two half-empty ones.
    """
    return PAIR_SEPARATOR.join(sorted((a, b)))


class MessageStore:
    def __init__(self, database: Database) -> None:
        self.db = database

    # ------------------------------------------------------------ writing ---

    def record(
        self,
        *,
        message_id: str,
        conversation: str,
        sender: str,
        recipient: str,
        body: str | None,
        nonce: str | None,
        ts: int,
    ) -> None:
        """Store one message. Ignores a repeat of an id already stored.

        A duplicate means the client resent after a reconnect, not that
        anything is wrong, so it is not an error.
        """
        with self.db.write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO messages"
                " (id, conversation, sender, recipient, body, nonce, ts)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (message_id, conversation, sender, recipient, body, nonce, ts),
            )

    def queue_for(self, username: str, message_id: str) -> None:
        """Note that `username` still owes a delivery of this message."""
        with self.db.write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO pending (message_id, username) VALUES (?, ?)",
                (message_id, username),
            )

    def mark(self, message_id: str, state: str, when: int) -> str | None:
        """Record that a message was delivered or read. Returns its sender.

        The sender is returned because the receipt is only of interest to
        them, and the caller would otherwise have to look the message up a
        second time to find out who to tell.

        A later receipt never overwrites an earlier one: `delivered` arriving
        after `read` would otherwise walk the state backwards when two
        receipts cross on the wire.
        """
        column = "read_at" if state == READ else "delivered_at"
        with self.db.write() as conn:
            row = conn.execute(
                "SELECT sender FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                f"UPDATE messages SET {column} = ? WHERE id = ? AND {column} IS NULL",
                (when, message_id),
            )
            # Being read implies having arrived, even if that receipt was lost.
            if state == READ:
                conn.execute(
                    "UPDATE messages SET delivered_at = ?"
                    " WHERE id = ? AND delivered_at IS NULL",
                    (when, message_id),
                )
        return str(row["sender"])

    def state_of(self, message_id: str) -> str:
        """SENT, DELIVERED or READ."""
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT delivered_at, read_at FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
        if row is None:
            return SENT
        if row["read_at"] is not None:
            return READ
        if row["delivered_at"] is not None:
            return DELIVERED
        return SENT

    # ------------------------------------------------------------ reading ---

    def flush(self, username: str) -> list[dict[str, Any]]:
        """Take everything queued for a user, oldest first, and clear it.

        Read and delete happen in one transaction: a crash between the two
        would either deliver twice or lose the message entirely.
        """
        with self.db.write() as conn:
            rows = conn.execute(
                "SELECT m.* FROM messages m"
                " JOIN pending p ON p.message_id = m.id"
                " WHERE p.username = ?"
                " ORDER BY m.ts, m.rowid",
                (username,),
            ).fetchall()
            conn.execute("DELETE FROM pending WHERE username = ?", (username,))
        return [dict(row) for row in rows]

    def pending_count(self, username: str) -> int:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM pending WHERE username = ?", (username,)
            ).fetchone()
        return int(row["n"])

    def history(
        self, conversation: str, before: int | None = None, limit: int = DEFAULT_LIMIT
    ) -> list[dict[str, Any]]:
        """The newest messages in a conversation, returned oldest first.

        `before` is a timestamp, so a client can page backwards by passing the
        oldest timestamp it already has. Selected newest-first so the index
        does the work, then reversed for display.

        Ties on `ts` break on rowid, which is insertion order. Two messages
        can easily share a millisecond, and breaking the tie on the frame id
        instead would order them by a random uuid -- so a conversation would
        come back in a different order each time it was read.
        """
        limit = max(1, min(int(limit), MAX_LIMIT))

        with self.db.read() as conn:
            if before is None:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE conversation = ?"
                    " ORDER BY ts DESC, rowid DESC LIMIT ?",
                    (conversation, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE conversation = ? AND ts < ?"
                    " ORDER BY ts DESC, rowid DESC LIMIT ?",
                    (conversation, int(before), limit),
                ).fetchall()

        return [dict(row) for row in reversed(rows)]

    def __len__(self) -> int:
        with self.db.read() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"])
