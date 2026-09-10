"""How long to wait before trying to reconnect.

Kept apart from the socket code for the same reason the state machine is: how
long to wait is a policy decision that should be readable and testable on its
own, without a network or a clock.

Exponential, because a server that is down stays down for a while and hammering
it helps nobody. Capped, because a client that gives up for an hour looks
broken to the person using it. Jittered, because without jitter every client
disconnected by the same event retries at the same instant, and the server
that just came back gets a thundering herd of them.
"""

from __future__ import annotations

import random

FIRST_DELAY = 1.0
FACTOR = 2.0
MAX_DELAY = 30.0

#: How much of a delay is randomised. 0.25 means the wait lands anywhere in
#: the last quarter below the nominal delay -- enough to spread a crowd out,
#: not so much that the schedule stops being predictable.
JITTER = 0.25


class Backoff:
    """The retry schedule for one connection.

    Reset on every successful connection, so a client that has been up for
    days does not start its next reconnect at half a minute.
    """

    def __init__(
        self,
        first: float = FIRST_DELAY,
        factor: float = FACTOR,
        maximum: float = MAX_DELAY,
        jitter: float = JITTER,
    ) -> None:
        self.first = first
        self.factor = factor
        self.maximum = maximum
        self.jitter = jitter
        self.attempts = 0

    def reset(self) -> None:
        self.attempts = 0

    def nominal(self) -> float:
        """The next delay before jitter. Useful for showing a countdown."""
        return min(self.first * (self.factor**self.attempts), self.maximum)

    def next_delay(self) -> float:
        """The next delay, with jitter, and advance the schedule."""
        delay = self.nominal()
        self.attempts += 1
        if self.jitter <= 0:
            return delay
        return delay * (1.0 - random.random() * self.jitter)
