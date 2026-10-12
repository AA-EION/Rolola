"""SQLite storage. One file in the data volume, WAL mode, versioned migrations.

All queries are tiny and indexed, so the main asyncio loop calls SQLite
synchronously; the library scanner uses its own connection in a thread.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'guest',          -- guest | proxy | api
        external_id TEXT UNIQUE,                      -- proxy identity
        role TEXT NOT NULL DEFAULT 'user',            -- user | admin
        banned INTEGER NOT NULL DEFAULT 0,
        lang TEXT,
        created_at REAL NOT NULL,
        last_seen REAL NOT NULL
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        is_admin INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        last_seen REAL NOT NULL
    );
    CREATE TABLE api_tokens (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        is_admin INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        last_used REAL
    );
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE queue (
        id INTEGER PRIMARY KEY,
        track_key TEXT NOT NULL,
        track TEXT NOT NULL,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        pos REAL NOT NULL,
        pinned INTEGER NOT NULL DEFAULT 0,
        added_at REAL NOT NULL
    );
    CREATE TABLE history (
        id INTEGER PRIMARY KEY,
        track_key TEXT NOT NULL,
        track TEXT NOT NULL,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        autoplay INTEGER NOT NULL DEFAULT 0,
        started_at REAL NOT NULL,
        ended_at REAL,
        outcome TEXT,                                 -- played | skipped | voted | error
        error TEXT,
        likes INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX history_started ON history(started_at DESC);
    CREATE INDEX history_user ON history(user_id, started_at DESC);
    CREATE TABLE favorites (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        track_key TEXT NOT NULL,
        track TEXT NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY (user_id, track_key)
    );
    CREATE TABLE playlists (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        shared INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL
    );
    CREATE TABLE playlist_items (
        id INTEGER PRIMARY KEY,
        playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
        track_key TEXT NOT NULL,
        track TEXT NOT NULL,
        pos REAL NOT NULL,
        added_by INTEGER REFERENCES users(id) ON DELETE SET NULL
    );
    CREATE INDEX playlist_items_pl ON playlist_items(playlist_id, pos);
    CREATE TABLE blocklist (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,                           -- track | artist | keyword
        value TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE loudness (
        cache_id TEXT PRIMARY KEY,
        lufs REAL,
        measured_at REAL NOT NULL
    );
    CREATE TABLE stations (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        tags TEXT,
        favicon TEXT,
        created_at REAL NOT NULL
    );
    CREATE TABLE library (
        id INTEGER PRIMARY KEY,
        path TEXT NOT NULL UNIQUE,
        mtime REAL NOT NULL,
        size INTEGER NOT NULL,
        title TEXT, artist TEXT, album TEXT, albumartist TEXT, genre TEXT,
        track_no INTEGER, disc_no INTEGER, year INTEGER,
        duration REAL,
        has_art INTEGER NOT NULL DEFAULT 0,
        rg_track_gain REAL
    );
    CREATE INDEX library_album ON library(albumartist, album, disc_no, track_no);
    CREATE VIRTUAL TABLE library_fts USING fts5(
        title, artist, album, genre, content='library', content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER library_ai AFTER INSERT ON library BEGIN
        INSERT INTO library_fts(rowid, title, artist, album, genre)
        VALUES (new.id, new.title, coalesce(new.artist,'') || ' ' || coalesce(new.albumartist,''), new.album, new.genre);
    END;
    CREATE TRIGGER library_ad AFTER DELETE ON library BEGIN
        INSERT INTO library_fts(library_fts, rowid, title, artist, album, genre)
        VALUES ('delete', old.id, old.title, coalesce(old.artist,'') || ' ' || coalesce(old.albumartist,''), old.album, old.genre);
    END;
    CREATE TRIGGER library_au AFTER UPDATE ON library BEGIN
        INSERT INTO library_fts(library_fts, rowid, title, artist, album, genre)
        VALUES ('delete', old.id, old.title, coalesce(old.artist,'') || ' ' || coalesce(old.albumartist,''), old.album, old.genre);
        INSERT INTO library_fts(rowid, title, artist, album, genre)
        VALUES (new.id, new.title, coalesce(new.artist,'') || ' ' || coalesce(new.albumartist,''), new.album, new.genre);
    END;
    """,
    # 2: ideas adopted from Spotube — cached cross-source matches, synced
    # lyrics (LRCLIB) and SponsorBlock segments
    """
    CREATE TABLE source_match (
        query_key TEXT PRIMARY KEY,                   -- key of the metadata-only track
        match TEXT NOT NULL,                          -- chosen playable Track
        alternatives TEXT NOT NULL DEFAULT '[]',      -- other candidates ("wrong match?")
        manual INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL
    );
    CREATE TABLE lyrics (
        track_key TEXT PRIMARY KEY,
        data TEXT,                                    -- null = looked up, none found
        fetched_at REAL NOT NULL
    );
    CREATE TABLE skip_segments (
        video_id TEXT PRIMARY KEY,
        segments TEXT NOT NULL,
        fetched_at REAL NOT NULL
    );
    """,
]


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self.conn = self.connect()
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def migrate(self) -> None:
        with self._lock:
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
                self.conn.executescript("BEGIN;" + sql + f"; PRAGMA user_version={i}; COMMIT;")

    # --- helpers -----------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, tuple(params))

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self._lock:
            self.conn.execute("BEGIN")
            try:
                self.conn.executemany(sql, rows)
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self._lock:
            row = self.conn.execute(sql, tuple(params)).fetchone()
            return dict(row) if row else None

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        with self._lock:
            row = self.conn.execute(sql, tuple(params)).fetchone()
            return row[0] if row else None

    def close(self) -> None:
        with self._lock:
            self.conn.close()


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def loads(raw: str | None, default: Any = None) -> Any:
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default
