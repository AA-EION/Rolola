"""Keeps Rolola maintenance-free: forgets idle devices and people,
ages out history and caches, and keeps the database file small."""

from __future__ import annotations

import time

from .auth import forget_guests, merge_duplicate_guests
from .db import Database
from .logs import get_logger

log = get_logger("housekeeping")
CACHE_TABLE_DAYS = 90       # lyrics, matches, SponsorBlock, loudness: cheap to re-fetch
HISTORY_MAX_ROWS = 5000     # hard cap, whatever the age


def cleanup(db: Database, guest_days: int, history_days: int, vacuum: bool = False) -> dict:
    now = time.time()
    day = 86400
    merged = merge_duplicate_guests(db)
    sessions = db.execute("DELETE FROM sessions WHERE last_seen < ?", (now - guest_days * day,)).rowcount
    stale = [r["id"] for r in db.all(
        "SELECT id FROM users WHERE kind='guest' AND last_seen < ?", (now - guest_days * day,))]
    # anyone without a signed-in device and nothing queued (e.g. signed out, incognito)
    stale += [r["id"] for r in db.all(
        "SELECT u.id FROM users u WHERE u.kind='guest' AND u.last_seen < ? "
        "AND NOT EXISTS (SELECT 1 FROM sessions s WHERE s.user_id=u.id)", (now - day,))]
    users = forget_guests(db, sorted(set(stale)))
    history = db.execute("DELETE FROM history WHERE started_at < ?", (now - history_days * day,)).rowcount
    history += db.execute(
        "DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY started_at DESC LIMIT ?)",
        (HISTORY_MAX_ROWS,)).rowcount
    old = now - CACHE_TABLE_DAYS * day
    for table, col in (("lyrics", "fetched_at"), ("skip_segments", "fetched_at"),
                       ("source_match", "created_at"), ("loudness", "measured_at")):
        db.execute(f"DELETE FROM {table} WHERE {col} < ?", (old,))
    if vacuum:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("VACUUM")
    result = {"merged": merged, "sessions": sessions, "users": users, "history": history}
    if any(result.values()):
        log.info("cleanup", extra={"ctx": result})
    return result
