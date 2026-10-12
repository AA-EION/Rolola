"""Local music library: a read-only folder mounted at ``MUSIC_DIR``.

A background, low-priority thread scans the folder (incrementally, by
mtime/size), reads tags with mutagen and indexes them in SQLite FTS5.
Works fully offline.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import threading
import time
from pathlib import Path

from ..db import Database
from ..logs import get_logger
from ..models import Playable, Track
from .base import ConfigField, Provider, ProviderError

log = get_logger("local")

AUDIO_EXT = {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".aac", ".wav", ".wma", ".aiff", ".aif", ".ape", ".wv", ".mpc"}
COVER_NAMES = ("cover", "folder", "front", "album", "albumart")
COVER_EXT = (".jpg", ".jpeg", ".png", ".webp")


def fts_query(text: str) -> str:
    tokens = [t for t in "".join(c if c.isalnum() else " " for c in text).split() if t]
    return " AND ".join(f'"{t}"*' for t in tokens[:8])


def _first(tags, key):
    v = tags.get(key) if tags else None
    if isinstance(v, list):
        v = v[0] if v else None
    return str(v).strip() if v not in (None, "") else None


def _int(v):
    if not v:
        return None
    try:
        return int(str(v).split("/")[0])
    except ValueError:
        return None


def _gain(v):
    if not v:
        return None
    try:
        return float(str(v).lower().replace("db", "").strip())
    except ValueError:
        return None


def read_tags(path: Path) -> dict:
    import mutagen

    info: dict = {}
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        f = None
    if f is not None:
        tags = f.tags or {}
        info = {
            "title": _first(tags, "title"),
            "artist": _first(tags, "artist"),
            "album": _first(tags, "album"),
            "albumartist": _first(tags, "albumartist"),
            "genre": _first(tags, "genre"),
            "track_no": _int(_first(tags, "tracknumber")),
            "disc_no": _int(_first(tags, "discnumber")),
            "year": _int((_first(tags, "date") or "")[:4]),
            "duration": float(getattr(f.info, "length", 0) or 0) or None,
            "rg_track_gain": _gain(_first(tags, "replaygain_track_gain")),
        }
    if not info.get("title"):
        info["title"] = path.stem
    return info


def find_folder_cover(folder: Path) -> Path | None:
    try:
        entries = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    except OSError:
        return None
    for n in COVER_NAMES:
        for e in COVER_EXT:
            if n + e in entries:
                return entries[n + e]
    return None


def embedded_art(path: Path) -> tuple[bytes, str] | None:
    import mutagen
    from mutagen.flac import Picture

    try:
        f = mutagen.File(path)
    except Exception:
        return None
    if f is None:
        return None
    pics = getattr(f, "pictures", None)
    if pics:
        return pics[0].data, pics[0].mime or "image/jpeg"
    tags = f.tags
    if tags is None:
        return None
    try:
        for k in tags.keys():
            if str(k).startswith("APIC"):
                fr = tags[k]
                return fr.data, fr.mime or "image/jpeg"
        if "covr" in tags:
            c = tags["covr"][0]
            return bytes(c), "image/png" if getattr(c, "imageformat", 13) == 14 else "image/jpeg"
        if "metadata_block_picture" in tags:
            pic = Picture(base64.b64decode(tags["metadata_block_picture"][0]))
            return pic.data, pic.mime or "image/jpeg"
    except Exception:
        return None
    return None


class LibraryScanner:
    def __init__(self, db_path: str, root: Path):
        self.db_path = db_path
        self.root = root
        self.lock = threading.Lock()
        self.running = False
        self.last_scan: float | None = None
        self.last_result: dict = {}
        self.progress = 0

    def scan(self) -> dict:
        if not self.lock.acquire(blocking=False):
            return {"busy": True}
        self.running = True
        try:
            try:  # keep the audio engine and web server responsive
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 15)
            except (OSError, AttributeError):
                pass
            return self._scan()
        finally:
            self.running = False
            self.lock.release()

    def _scan(self) -> dict:
        t0 = time.time()
        db = Database(self.db_path)
        added = updated = removed = errors = 0
        try:
            if not self.root.is_dir():
                self.last_result = {"error": f"{self.root} is not mounted"}
                return self.last_result
            known = {r["path"]: (r["mtime"], r["size"]) for r in db.all("SELECT path, mtime, size FROM library")}
            seen: set[str] = set()
            batch: list[tuple] = []
            self.progress = 0

            def flush():
                nonlocal batch
                if batch:
                    db.executemany(
                        "INSERT INTO library(path, mtime, size, title, artist, album, albumartist, genre, track_no,"
                        " disc_no, year, duration, rg_track_gain) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                        " ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size,"
                        " title=excluded.title, artist=excluded.artist, album=excluded.album,"
                        " albumartist=excluded.albumartist, genre=excluded.genre, track_no=excluded.track_no,"
                        " disc_no=excluded.disc_no, year=excluded.year, duration=excluded.duration,"
                        " rg_track_gain=excluded.rg_track_gain",
                        batch,
                    )
                    batch = []

            for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fn in filenames:
                    p = Path(dirpath) / fn
                    if p.suffix.lower() not in AUDIO_EXT or fn.startswith("."):
                        continue
                    rel = str(p.relative_to(self.root))
                    seen.add(rel)
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    prev = known.get(rel)
                    if prev and abs(prev[0] - st.st_mtime) < 1 and prev[1] == st.st_size:
                        continue
                    try:
                        t = read_tags(p)
                    except Exception:
                        errors += 1
                        continue
                    batch.append((rel, st.st_mtime, st.st_size, t.get("title"), t.get("artist"), t.get("album"),
                                  t.get("albumartist"), t.get("genre"), t.get("track_no"), t.get("disc_no"),
                                  t.get("year"), t.get("duration"), t.get("rg_track_gain")))
                    if prev:
                        updated += 1
                    else:
                        added += 1
                    self.progress += 1
                    if len(batch) >= 200:
                        flush()
            flush()
            gone = [k for k in known if k not in seen]
            for i in range(0, len(gone), 500):
                chunk = gone[i : i + 500]
                db.execute(f"DELETE FROM library WHERE path IN ({','.join('?' * len(chunk))})", chunk)
            removed = len(gone)
            total = db.scalar("SELECT COUNT(*) FROM library")
        finally:
            db.close()
        self.last_scan = time.time()
        self.last_result = {"added": added, "updated": updated, "removed": removed, "errors": errors,
                            "total": total, "seconds": round(time.time() - t0, 1)}
        log.info("library scan done", extra={"ctx": self.last_result})
        return self.last_result


class LocalProvider(Provider):
    id = "local"
    name = "Local library"
    description = "Music files on the NAS (read-only folder)."
    account = "None."
    legal = "Your own files. Make sure you're licensed for public performance in your office if required in your country."
    risk = "low"
    needs_internet = False
    default_enabled = True
    capabilities = ("search", "albums", "random", "similar", "offline")
    config_fields = [ConfigField("enabled", "Enabled", "bool", default=True)]

    def __init__(self, services):
        super().__init__(services)
        self.root: Path = services.config.music_dir
        self.scanner = LibraryScanner(str(services.config.db_path), self.root)

    def current_status(self):
        if not self.enabled:
            return "disabled", ""
        if not self.root.is_dir():
            return "broken", f"Folder {self.root} is not mounted."
        if self.scanner.running:
            return "connected", f"Scanning… ({self.scanner.progress} files processed)"
        n = self.svc.db.scalar("SELECT COUNT(*) FROM library") or 0
        return "connected", f"{n} tracks"

    async def start(self) -> None:
        asyncio.get_running_loop().run_in_executor(None, self.scanner.scan)

    async def rescan(self) -> dict:
        return await asyncio.get_running_loop().run_in_executor(None, self.scanner.scan)

    # --- conversions ----------------------------------------------------------
    def _track(self, r: dict) -> Track:
        return Track(
            source="local", id=r["path"], title=r["title"] or Path(r["path"]).stem,
            artist=r["artist"] or r["albumartist"], album=r["album"], duration=r["duration"],
            artwork=f"api/art/local/{r['id']}",
            extra={"rg": r["rg_track_gain"] is not None, "lib_id": r["id"], "genre": r["genre"]},
        )

    def _album(self, r: dict) -> Track:
        artist = r["albumartist"] or r["artist"] or ""
        return Track(
            source="local", id=f"album|{artist}|{r['album']}", kind="album", title=r["album"],
            artist=artist or None, artwork=f"api/art/local/{r['id']}", count=r["n"],
        )

    async def search(self, query: str, limit: int = 20) -> list[Track]:
        q = fts_query(query)
        if not q:
            return []
        db = self.svc.db
        rows = db.all(
            "SELECT l.* FROM library_fts f JOIN library l ON l.id=f.rowid WHERE library_fts MATCH ? "
            "ORDER BY bm25(library_fts, 10.0, 5.0, 3.0, 1.0) LIMIT ?",
            (q, limit),
        )
        albums = db.all(
            "SELECT l.id, l.album, l.albumartist, l.artist, COUNT(*) AS n FROM library_fts f JOIN library l ON l.id=f.rowid "
            "WHERE library_fts MATCH ? AND l.album IS NOT NULL AND l.album != '' "
            "GROUP BY COALESCE(l.albumartist, l.artist), l.album HAVING n > 1 ORDER BY n DESC LIMIT 5",
            (q,),
        )
        self.ok()
        return [self._album(a) for a in albums] + [self._track(r) for r in rows]

    async def expand(self, item: Track) -> list[Track]:
        if item.kind != "album":
            return [item]
        _, artist, album = item.id.split("|", 2)
        rows = self.svc.db.all(
            "SELECT * FROM library WHERE album=? AND COALESCE(albumartist, artist, '')=? "
            "ORDER BY disc_no, track_no, path",
            (album, artist),
        )
        return [self._track(r) for r in rows]

    def _abs(self, rel: str) -> Path:
        root = self.root.resolve()
        p = (root / rel).resolve()
        if not p.is_relative_to(root):
            raise ProviderError("Invalid path.")
        return p

    async def resolve(self, track: Track) -> Playable:
        p = self._abs(track.id)
        if not p.is_file():
            raise ProviderError("The file is no longer in the library.")
        st = p.stat()
        cid = "local-" + hashlib.sha1(f"{track.id}|{st.st_mtime}|{st.st_size}".encode()).hexdigest()[:20]
        return Playable(target=str(p), cache_id=None if track.extra.get("rg") else cid)

    async def random_tracks(self, limit: int = 10) -> list[Track]:
        rows = self.svc.db.all("SELECT * FROM library ORDER BY RANDOM() LIMIT ?", (limit,))
        return [self._track(r) for r in rows]

    async def similar(self, track: Track, limit: int = 10) -> list[Track]:
        db = self.svc.db
        rows: list[dict] = []
        if track.artist:
            rows = db.all("SELECT * FROM library WHERE (artist=? OR albumartist=?) AND path!=? ORDER BY RANDOM() LIMIT ?",
                          (track.artist, track.artist, track.id if track.source == "local" else "", limit // 2 or 1))
        genre = track.extra.get("genre")
        if genre:
            rows += db.all("SELECT * FROM library WHERE genre=? ORDER BY RANDOM() LIMIT ?", (genre, limit - len(rows)))
        return [self._track(r) for r in rows]

    async def test(self) -> str:
        if not self.root.is_dir():
            raise ProviderError(f"{self.root} is not mounted.")
        return f"{self.svc.db.scalar('SELECT COUNT(*) FROM library')} tracks indexed"

    def art_for(self, lib_id: int) -> tuple[bytes, str] | Path | None:
        row = self.svc.db.one("SELECT path FROM library WHERE id=?", (lib_id,))
        if not row:
            return None
        p = self._abs(row["path"])
        cover = find_folder_cover(p.parent)
        if cover:
            return cover
        return embedded_art(p)
