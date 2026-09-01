"""Identifier and timestamp generation for frames."""

from __future__ import annotations

import time
import uuid


def new_id() -> str:
    """A unique frame identifier.

    Used to correlate an ACK with the message it acknowledges, and to drop
    duplicates when a client resends after a reconnect. Random rather than
    sequential: two clients generate ids independently and must never collide.
    """
    return uuid.uuid4().hex


def now_ms() -> int:
    """Milliseconds since the Unix epoch.

    Milliseconds because chat ordering within a second matters, and an int
    because it has to survive a JSON round trip unchanged.
    """
    return int(time.time() * 1000)
