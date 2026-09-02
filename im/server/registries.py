"""Who is connected, and who is in which room.

Both registries are plain dicts behind a threading.Lock. The GIL is not a
substitute for that lock. It makes a single dict operation atomic, but every
interesting operation here is compound -- "is this name taken, and if not,
claim it" -- and two threads can interleave between the read and the write.
Each such sequence happens inside one `with self._lock` block.

Methods that return a collection return a *copy*, so the caller can iterate
and send without holding the lock. Holding a lock while sending would put the
slowest client on the critical path of every other delivery.
"""

from __future__ import annotations

import threading
from typing import Protocol, runtime_checkable

from im.common.frames import Frame


@runtime_checkable
class Session(Protocol):
    """What the router needs from a connection.

    Deliberately not a socket. `send` must not block -- the real
    implementation drops the frame on a queue for a writer thread to deal
    with -- which is exactly what lets the router be tested against a fake
    that appends to a list, with no network anywhere.
    """

    username: str | None

    def send(self, frame: Frame) -> None: ...


class SessionRegistry:
    """The logged-in connections, keyed by username."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_name: dict[str, Session] = {}

    def login(self, username: str, session: Session) -> bool:
        """Claim a username. False if somebody is already using it."""
        with self._lock:
            if username in self._by_name:
                return False
            self._by_name[username] = session
            return True

    def logout(self, username: str) -> None:
        with self._lock:
            self._by_name.pop(username, None)

    def get(self, username: str) -> Session | None:
        with self._lock:
            return self._by_name.get(username)

    def is_online(self, username: str) -> bool:
        with self._lock:
            return username in self._by_name

    def usernames(self) -> list[str]:
        with self._lock:
            return sorted(self._by_name)

    def others(self, username: str) -> list[Session]:
        """Every logged-in session except this user's own.

        A snapshot, so presence can be fanned out after the lock is released.
        """
        with self._lock:
            return [s for name, s in self._by_name.items() if name != username]

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_name)


class RoomRegistry:
    """Room membership. Room names carry a leading '#'.

    Populated by hand in phase 2 and by CREATE_ROOM / JOIN / LEAVE in phase 4.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._members: dict[str, set[str]] = {}

    def create(self, room: str) -> bool:
        """Create an empty room. False if it already exists."""
        with self._lock:
            if room in self._members:
                return False
            self._members[room] = set()
            return True

    def join(self, room: str, username: str) -> None:
        """Add a member, creating the room if it is new."""
        with self._lock:
            self._members.setdefault(room, set()).add(username)

    def leave(self, room: str, username: str) -> None:
        with self._lock:
            members = self._members.get(room)
            if members is not None:
                members.discard(username)

    def members(self, room: str) -> set[str]:
        """A copy of the membership, safe to iterate outside the lock."""
        with self._lock:
            return set(self._members.get(room, ()))

    def exists(self, room: str) -> bool:
        with self._lock:
            return room in self._members

    def rooms_of(self, username: str) -> list[str]:
        with self._lock:
            return sorted(room for room, members in self._members.items() if username in members)

    def forget(self, username: str) -> None:
        """Drop a user from every room. Called when they disconnect."""
        with self._lock:
            for members in self._members.values():
                members.discard(username)
