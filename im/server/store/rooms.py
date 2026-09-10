"""Rooms and their membership, on disk.

Same methods as the in-memory RoomRegistry, so the router cannot tell which
one it was handed and the routing tests keep using the fast one.

The important difference is not speed, it is lifetime. Membership used to be
dropped when a connection closed, which made LOGIN_OK's room list always
empty and meant a room message could never wait for an absent member. Here it
persists: you are in a room until you LEAVE, whether or not you are connected.
"""

from __future__ import annotations

import time

from im.server.store.db import Database


class SqliteRooms:
    def __init__(self, database: Database) -> None:
        self.db = database

    def create(self, room: str) -> bool:
        """Create an empty room. False if it already exists."""
        with self.db.write() as conn:
            existing = conn.execute("SELECT 1 FROM rooms WHERE room = ?", (room,)).fetchone()
            if existing is not None:
                return False
            conn.execute(
                "INSERT INTO rooms (room, created_at) VALUES (?, ?)",
                (room, int(time.time() * 1000)),
            )
        return True

    def join(self, room: str, username: str) -> None:
        """Add a member, creating the room if it is new.

        INSERT OR IGNORE rather than a check followed by a write: joining a
        room you are already in is a harmless repeat, not an error.
        """
        with self.db.write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO rooms (room, created_at) VALUES (?, ?)",
                (room, int(time.time() * 1000)),
            )
            conn.execute(
                "INSERT OR IGNORE INTO memberships (room, username) VALUES (?, ?)",
                (room, username),
            )

    def leave(self, room: str, username: str) -> None:
        with self.db.write() as conn:
            conn.execute(
                "DELETE FROM memberships WHERE room = ? AND username = ?", (room, username)
            )

    def members(self, room: str) -> set[str]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT username FROM memberships WHERE room = ?", (room,)
            ).fetchall()
        return {row["username"] for row in rows}

    def exists(self, room: str) -> bool:
        with self.db.read() as conn:
            row = conn.execute("SELECT 1 FROM rooms WHERE room = ?", (room,)).fetchone()
        return row is not None

    def rooms_of(self, username: str) -> list[str]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT room FROM memberships WHERE username = ? ORDER BY room", (username,)
            ).fetchall()
        return [row["room"] for row in rows]

    def forget(self, username: str) -> None:
        """Remove a user from every room.

        Deliberately NOT called on disconnect any more -- see the module
        docstring. It remains for account deletion, which nothing calls yet.
        """
        with self.db.write() as conn:
            conn.execute("DELETE FROM memberships WHERE username = ?", (username,))
