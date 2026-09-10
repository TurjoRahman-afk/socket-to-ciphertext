"""The sqlite database: connection handling and schema.

One connection, shared by every thread, guarded by a lock.

sqlite3 refuses cross-thread use by default, and the alternative -- a
connection per thread -- would need its own pool and would still serialise on
sqlite's own file locking. At this scale (a handful of clients, a write per
message) one guarded connection is simpler and fast enough, and it makes the
ordering of writes obvious rather than something to reason about.

Every statement here is written out in full rather than generated. The point
of this phase is the SQL, not an object-relational mapper.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

#: Passed to sqlite3.connect for an in-memory database, used by the tests.
MEMORY = ":memory:"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username    TEXT PRIMARY KEY,
    salt        BLOB    NOT NULL,
    digest      BLOB    NOT NULL,
    pubkey      TEXT,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rooms (
    room        TEXT PRIMARY KEY,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS memberships (
    room        TEXT NOT NULL,
    username    TEXT NOT NULL,
    PRIMARY KEY (room, username)
);

CREATE TABLE IF NOT EXISTS messages (
    id            TEXT PRIMARY KEY,
    conversation  TEXT    NOT NULL,
    sender        TEXT    NOT NULL,
    recipient     TEXT    NOT NULL,
    body          TEXT,
    nonce         TEXT,
    ts            INTEGER NOT NULL
);

-- History is always read as "the newest N in this conversation", so the
-- index carries the ordering column too and the query never sorts a table.
CREATE INDEX IF NOT EXISTS ix_messages_conversation
    ON messages (conversation, ts);

CREATE TABLE IF NOT EXISTS pending (
    message_id  TEXT NOT NULL,
    username    TEXT NOT NULL,
    PRIMARY KEY (message_id, username)
);

CREATE INDEX IF NOT EXISTS ix_pending_username
    ON pending (username);
"""


class Database:
    """A sqlite connection plus the lock that makes it safe to share."""

    def __init__(self, path: str | Path = MEMORY) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self.path,
            # Safe only because every use goes through the lock below.
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        # Foreign keys are off by default in sqlite, and write-ahead logging
        # lets a reader run while a write is in flight.
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != MEMORY:
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """A transaction. Commits on success, rolls back on an exception."""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()
