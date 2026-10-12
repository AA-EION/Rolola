"""Audio cache and loudness analysis.

Remote tracks (YouTube, SoundCloud, ...) are downloaded to the data volume
before they play, so playback never depends on the network mid-song and the
next track can be preloaded for a gapless start.  The cache is an LRU capped
at ``CACHE_MAX_MB``.  Every cached or local file gets an EBU R128 loudness
measurement (low-priority ffmpeg) so tracks play at the same level.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from pathlib import Path
from typing import Awaitable, Callable

from .db import Database
from .logs import get_logger
from .ytdlp import run_lowprio

log = get_logger("cache")
_LUFS_RE = re.compile(r"I:\s+(-?[\d.]+)\s+LUFS")


def cache_id_for(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:20]


class AudioCache:
    def __init__(self, root: Path, db: Database, max_mb: int, max_parallel: int = 2):
        self.root = Path(root) / "audio"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = db
        self.max_bytes = max(64, max_mb) * 1024 * 1024
        self._sem = asyncio.Semaphore(max_parallel)
        self._inflight: dict[str, asyncio.Task] = {}
        self._loud_inflight: dict[str, asyncio.Task] = {}
        # clean partial files from a previous crash
        for p in self.root.glob("*.part"):
            p.unlink(missing_ok=True)

    def lookup(self, key: str) -> Path | None:
        cid = cache_id_for(key)
        for p in self.root.glob(cid + ".*"):
            if p.suffix not in (".part", ".ytdl") and p.stat().st_size > 0:
                try:
                    p.touch()
                except OSError:
                    pass
                return p
        return None

    def is_pending(self, key: str) -> bool:
        t = self._inflight.get(cache_id_for(key))
        return bool(t and not t.done())

    async def ensure(self, key: str, fetch: Callable[[Path], Awaitable[Path]]) -> Path:
        """Return the cached file for ``key``, downloading it once if needed.
        Concurrent callers share the same download."""
        hit = self.lookup(key)
        if hit:
            return hit
        cid = cache_id_for(key)
        task = self._inflight.get(cid)
        if task is None or task.done():
            task = asyncio.create_task(self._download(cid, fetch))
            self._inflight[cid] = task
        return await asyncio.shield(task)

    async def _download(self, cid: str, fetch: Callable[[Path], Awaitable[Path]]) -> Path:
        try:
            async with self._sem:
                t0 = time.monotonic()
                path = await fetch(self.root / cid)
                log.info("cached %s in %.1fs", path.name, time.monotonic() - t0)
            self.trim()
            return path
        finally:
            self._inflight.pop(cid, None)

    def usage(self) -> tuple[int, int]:
        files = [p for p in self.root.iterdir() if p.is_file()]
        return sum(p.stat().st_size for p in files), len(files)

    def trim(self, keep: set[str] | None = None) -> None:
        files = sorted((p for p in self.root.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for p in files:
            if total <= self.max_bytes:
                break
            if keep and p.stem in keep:
                continue
            total -= p.stat().st_size
            p.unlink(missing_ok=True)

    def clear(self) -> None:
        for p in self.root.iterdir():
            if p.is_file():
                p.unlink(missing_ok=True)

    # --- loudness ---------------------------------------------------------
    def loudness(self, cid: str) -> float | None:
        row = self.db.one("SELECT lufs FROM loudness WHERE cache_id=?", (cid,))
        return row["lufs"] if row else None

    def has_loudness(self, cid: str) -> bool:
        return self.db.one("SELECT 1 FROM loudness WHERE cache_id=?", (cid,)) is not None

    def measure_soon(self, cid: str, path: Path) -> asyncio.Task | None:
        if self.has_loudness(cid):
            return None
        task = self._loud_inflight.get(cid)
        if task is None or task.done():
            task = asyncio.create_task(self._measure(cid, path))
            self._loud_inflight[cid] = task
        return task

    async def _measure(self, cid: str, path: Path) -> float | None:
        lufs: float | None = None
        try:
            async with self._sem:
                code, _, err = await run_lowprio(
                    ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-threads", "1", "-i", str(path),
                     "-vn", "-af", "ebur128", "-f", "null", "-"],
                    timeout=180,
                )
            matches = _LUFS_RE.findall(err.decode(errors="replace"))
            if code == 0 and matches:
                lufs = float(matches[-1])
                if lufs < -70:  # silence / failed analysis
                    lufs = None
        except Exception as exc:
            log.warning("loudness analysis failed for %s: %s", path.name, exc)
        self.db.execute(
            "INSERT OR REPLACE INTO loudness(cache_id, lufs, measured_at) VALUES (?,?,?)", (cid, lufs, time.time())
        )
        self._loud_inflight.pop(cid, None)
        return lufs
