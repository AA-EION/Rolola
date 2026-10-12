"""Blocklist and quiet-hours schedule."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from .db import Database
from .models import Track


class Blocklist:
    KINDS = ("track", "artist", "keyword")

    def __init__(self, db: Database):
        self.db = db
        self._rows: list[dict] = []
        self.reload()

    def reload(self) -> None:
        self._rows = self.db.all("SELECT * FROM blocklist ORDER BY created_at DESC")

    def all(self) -> list[dict]:
        return list(self._rows)

    def add(self, kind: str, value: str) -> None:
        value = value.strip()
        if kind not in self.KINDS or not value or len(value) > 300:
            raise ValueError("err.bad_request")
        self.db.execute("INSERT INTO blocklist(kind, value, created_at) VALUES (?,?,?)", (kind, value, time.time()))
        self.reload()

    def remove(self, rule_id: int) -> None:
        self.db.execute("DELETE FROM blocklist WHERE id=?", (rule_id,))
        self.reload()

    def reason(self, track: Track) -> str | None:
        """Why this track is blocked, or None."""
        keys = {track.key}
        if track.substituted_from:
            keys.add(track.substituted_from.get("key", ""))
        title = (track.title or "").lower()
        artist = (track.artist or "").lower()
        for r in self._rows:
            v = r["value"].lower()
            if r["kind"] == "track" and r["value"] in keys:
                return "track"
            if r["kind"] == "artist" and v and (v == artist or v in artist):
                return f"artist: {r['value']}"
            if r["kind"] == "keyword" and v and (v in title or v in artist):
                return f"keyword: {r['value']}"
        return None


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def schedule_allows(schedule: list[dict], now: datetime | None = None) -> bool:
    """True when ``now`` falls inside any window. Windows may cross midnight
    (start > end); the day refers to the day the window starts."""
    now = now or datetime.now()
    minute = now.hour * 60 + now.minute
    today = now.weekday()
    yesterday = (today - 1) % 7
    for w in schedule or []:
        try:
            start, end = _minutes(w["start"]), _minutes(w["end"])
        except (KeyError, ValueError):
            continue
        days = w.get("days") or list(range(7))
        if start <= end:
            if today in days and start <= minute < end:
                return True
        else:
            if today in days and minute >= start:
                return True
            if yesterday in days and minute < end:
                return True
    return False


def next_window_start(schedule: list[dict], now: datetime | None = None) -> datetime | None:
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    for step in range(1, 8 * 24 * 60, 5):
        t = now + timedelta(minutes=step)
        if schedule_allows(schedule, t):
            return t
    return None
