"""The shared queue.

Storage is a flat table; the *play order* is computed on demand:

* pinned items ("play next", admin only) come first, in ``pos`` order;
* with fairness off, the rest follows ``pos`` order;
* with fairness on, every user has a personal sub-queue (ordered by ``pos``)
  and the global order interleaves them round-robin.  Within each round,
  users who were served longest ago go first; ties go to whoever queued
  first.  One person adding 50 tracks therefore only gets every N-th slot.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .db import Database, dumps, loads
from .models import Track


@dataclass
class QueueItem:
    id: int
    track: Track
    user_id: int | None
    pos: float
    pinned: bool
    added_at: float


class QueueError(Exception):
    """User-facing error (message is an i18n key + params)."""

    def __init__(self, code: str, **params):
        super().__init__(code)
        self.code = code
        self.params = params


def order_items(
    items: list[QueueItem],
    fairness: bool,
    last_served: dict[int | None, float] | None = None,
) -> list[QueueItem]:
    last_served = last_served or {}
    pinned = sorted((i for i in items if i.pinned), key=lambda i: (i.pos, i.id))
    rest = [i for i in items if not i.pinned]
    if not fairness:
        return pinned + sorted(rest, key=lambda i: (i.pos, i.id))

    per_user: dict[int | None, list[QueueItem]] = {}
    for item in sorted(rest, key=lambda i: (i.pos, i.id)):
        per_user.setdefault(item.user_id, []).append(item)

    first_added = {u: min(i.added_at for i in lst) for u, lst in per_user.items()}
    users = sorted(
        per_user,
        key=lambda u: (last_served.get(u, 0.0), first_added[u], -1 if u is None else u),
    )
    ordered: list[QueueItem] = []
    depth = max((len(v) for v in per_user.values()), default=0)
    for r in range(depth):
        for u in users:
            lst = per_user[u]
            if r < len(lst):
                ordered.append(lst[r])
    return pinned + ordered


class QueueManager:
    def __init__(
        self,
        db: Database,
        get_setting: Callable[[str], object],
        on_change: Callable[[], None] | None = None,
    ):
        self.db = db
        self.get_setting = get_setting
        self.on_change = on_change or (lambda: None)
        self.last_served: dict[int | None, float] = {}
        for row in db.all(
            "SELECT user_id, MAX(started_at) AS t FROM history WHERE autoplay=0 GROUP BY user_id"
        ):
            self.last_served[row["user_id"]] = row["t"]

    # --- reads -------------------------------------------------------------
    def _rows(self) -> list[QueueItem]:
        return [
            QueueItem(
                id=r["id"],
                track=Track.model_validate(loads(r["track"])),
                user_id=r["user_id"],
                pos=r["pos"],
                pinned=bool(r["pinned"]),
                added_at=r["added_at"],
            )
            for r in self.db.all("SELECT * FROM queue")
        ]

    def ordered(self) -> list[QueueItem]:
        return order_items(self._rows(), bool(self.get_setting("fairness_enabled")), self.last_served)

    def head(self) -> QueueItem | None:
        items = self.ordered()
        return items[0] if items else None

    def get(self, item_id: int) -> QueueItem | None:
        for item in self._rows():
            if item.id == item_id:
                return item
        return None

    def count_for_user(self, user_id: int) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM queue WHERE user_id=?", (user_id,)) or 0)

    def __len__(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM queue") or 0)

    # --- writes ------------------------------------------------------------
    def _next_pos(self) -> float:
        return float(self.db.scalar("SELECT COALESCE(MAX(pos), 0) FROM queue") or 0) + 1.0

    def add(self, tracks: list[Track], user_id: int, is_admin: bool = False) -> tuple[list[QueueItem], int]:
        """Append tracks. Returns (added items, number dropped by the per-user limit)."""
        limit = int(self.get_setting("max_queued_per_user") or 0)
        allowed = len(tracks)
        if limit > 0 and not is_admin:
            room = max(0, limit - self.count_for_user(user_id))
            if room == 0:
                raise QueueError("err.queue_limit", limit=limit)
            allowed = min(allowed, room)
        now = time.time()
        pos = self._next_pos()
        added: list[QueueItem] = []
        for t in tracks[:allowed]:
            cur = self.db.execute(
                "INSERT INTO queue(track_key, track, user_id, pos, pinned, added_at) VALUES (?,?,?,?,0,?)",
                (t.key, dumps(t.model_dump()), user_id, pos, now),
            )
            added.append(QueueItem(cur.lastrowid, t, user_id, pos, False, now))
            pos += 1.0
            now += 1e-6  # keep insertion order stable
        if added:
            self.on_change()
        return added, len(tracks) - allowed

    def remove(self, item_id: int, user_id: int | None, is_admin: bool) -> QueueItem:
        item = self.get(item_id)
        if item is None:
            raise QueueError("err.not_found")
        if not is_admin and item.user_id != user_id:
            raise QueueError("err.not_yours")
        self.db.execute("DELETE FROM queue WHERE id=?", (item_id,))
        self.on_change()
        return item

    def take(self, item_id: int) -> QueueItem | None:
        """Remove an item because it is about to play."""
        item = self.get(item_id)
        if item:
            self.db.execute("DELETE FROM queue WHERE id=?", (item_id,))
            self.on_change()
        return item

    def clear(self, user_id: int | None = None) -> int:
        if user_id is None:
            n = self.db.execute("DELETE FROM queue").rowcount
        else:
            n = self.db.execute("DELETE FROM queue WHERE user_id=?", (user_id,)).rowcount
        self.on_change()
        return n

    def mark_served(self, user_id: int | None, at: float | None = None) -> None:
        if user_id is not None:
            self.last_served[user_id] = at or time.time()

    def move(self, item_id: int, action: str, user_id: int | None, is_admin: bool) -> None:
        """action: up | down | top | unpin.

        Normal users only reorder their own items relative to each other.
        Admins move in the global order; with fairness on, ``top`` pins the
        item ("play next") because the interleaving is computed.
        """
        item = self.get(item_id)
        if item is None:
            raise QueueError("err.not_found")
        if not is_admin and item.user_id != user_id:
            raise QueueError("err.not_yours")
        fairness = bool(self.get_setting("fairness_enabled"))

        if action == "unpin":
            if not is_admin:
                raise QueueError("err.admin_only")
            self.db.execute("UPDATE queue SET pinned=0 WHERE id=?", (item_id,))
            self.on_change()
            return

        if is_admin and action == "top":
            low = self.db.scalar("SELECT MIN(pos) FROM queue WHERE pinned=1")
            low = (low if low is not None else item.pos) - 1.0
            self.db.execute("UPDATE queue SET pinned=1, pos=? WHERE id=?", (low, item_id))
            self.on_change()
            return

        if is_admin and not fairness:
            peers = [i for i in self.ordered() if i.pinned == item.pinned]
        else:
            peers = sorted(
                (i for i in self._rows() if i.user_id == item.user_id and i.pinned == item.pinned),
                key=lambda i: (i.pos, i.id),
            )
        idx = next(n for n, i in enumerate(peers) if i.id == item_id)
        if action == "top":
            if idx == 0:
                return
            new_pos = peers[0].pos - 1.0
            self.db.execute("UPDATE queue SET pos=? WHERE id=?", (new_pos, item_id))
        elif action in ("up", "down"):
            j = idx - 1 if action == "up" else idx + 1
            if j < 0 or j >= len(peers):
                return
            other = peers[j]
            a, b = item.pos, other.pos
            if a == b:  # should not happen, but keep the swap meaningful
                b = a + (-0.5 if action == "up" else 0.5)
            self.db.execute("UPDATE queue SET pos=? WHERE id=?", (b, item.id))
            self.db.execute("UPDATE queue SET pos=? WHERE id=?", (a, other.id))
        else:
            raise QueueError("err.bad_request")
        self.on_change()
