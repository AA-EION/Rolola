"""Skip voting. Pure logic, no I/O, so it is easy to test."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def required_votes(listeners: int, mode: str, value: float) -> int:
    """How many votes skip the current track.

    mode ``percent``: ``value`` percent of connected listeners (rounded up).
    mode ``count``: a fixed number, capped at the number of listeners so a
    small room can still skip.
    Always at least 1.
    """
    listeners = max(listeners, 1)
    if mode == "count":
        return max(1, min(int(value), listeners))
    pct = min(max(float(value), 1.0), 100.0)
    return max(1, math.ceil(listeners * pct / 100.0 - 1e-9))


@dataclass
class VoteBox:
    play_id: int | None = None
    voters: set[int] = field(default_factory=set)

    def reset(self, play_id: int | None) -> None:
        self.play_id = play_id
        self.voters = set()

    def vote(self, user_id: int) -> bool:
        """Returns False if the user had already voted."""
        if user_id in self.voters:
            return False
        self.voters.add(user_id)
        return True

    def unvote(self, user_id: int) -> None:
        self.voters.discard(user_id)

    def passed(self, listeners: int, mode: str, value: float) -> bool:
        return len(self.voters) >= required_votes(listeners, mode, value)
