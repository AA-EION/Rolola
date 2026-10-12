"""Time-synced lyrics from LRCLIB (https://lrclib.net), free and keyless —
the same service Spotube uses.  Results (including "none found") are cached
in SQLite so each song is looked up once."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

import httpx

from .db import dumps, loads
from .models import Track
from .net import USER_AGENT

if TYPE_CHECKING:
    from .services import Services

LRC_LINE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
RETRY_EMPTY_AFTER = 7 * 86400


def parse_lrc(text: str) -> list[dict]:
    """``[mm:ss.xx]line`` -> [{"t": seconds, "text": line}], sorted.
    Lines carrying several timestamps are repeated at each of them."""
    out = []
    for raw in (text or "").splitlines():
        stamps = LRC_LINE.findall(raw)
        if not stamps:
            continue
        line = LRC_LINE.sub("", raw).strip()
        for m, s in stamps:
            out.append({"t": round(int(m) * 60 + float(s), 2), "text": line})
    return sorted(out, key=lambda x: x["t"])


class Lyrics:
    def __init__(self, services: "Services"):
        self.svc = services

    def _query_track(self, track: Track) -> Track:
        # a substituted track: ask for the song people picked, not the YouTube title
        sub = track.substituted_from
        if sub:
            return Track(source=sub["source"], id=sub["id"], title=sub["title"], artist=sub.get("artist"),
                         duration=track.duration)
        return track

    async def get(self, track: Track) -> dict | None:
        q = self._query_track(track)
        row = self.svc.db.one("SELECT * FROM lyrics WHERE track_key=?", (q.key,))
        if row and (row["data"] or time.time() - row["fetched_at"] < RETRY_EMPTY_AFTER):
            return loads(row["data"])
        if q.live or not q.title:
            return None
        data = await self._fetch(q)
        self.svc.db.execute("INSERT OR REPLACE INTO lyrics(track_key, data, fetched_at) VALUES (?,?,?)",
                            (q.key, dumps(data) if data else None, time.time()))
        return data

    async def _fetch(self, t: Track) -> dict | None:
        headers = {"User-Agent": USER_AGENT}
        res: dict | None = None
        try:
            if t.artist:
                params = {"artist_name": t.artist, "track_name": t.title}
                if t.album:
                    params["album_name"] = t.album
                if t.duration:
                    params["duration"] = str(int(t.duration))
                r = await self.svc.http.get("https://lrclib.net/api/get", params=params, headers=headers, timeout=8)
                if r.status_code == 200:
                    res = r.json()
            if res is None:  # fuzzy fallback (YouTube titles, missing album...)
                r = await self.svc.http.get("https://lrclib.net/api/search",
                                            params={"q": f"{t.artist or ''} {t.title}".strip()},
                                            headers=headers, timeout=8)
                if r.status_code == 200:
                    hits = r.json() or []
                    if t.duration:
                        hits = [h for h in hits if abs((h.get("duration") or 0) - t.duration) < 15]
                    res = next((h for h in hits if h.get("syncedLyrics")), hits[0] if hits else None)
        except (httpx.HTTPError, ValueError):
            return None  # offline: don't cache the miss
        if not res:
            return None
        if res.get("instrumental"):
            return {"instrumental": True, "lines": [], "synced": False, "provider": "LRCLIB"}
        synced = parse_lrc(res.get("syncedLyrics") or "")
        if synced:
            return {"synced": True, "lines": synced, "provider": "LRCLIB"}
        plain = [l for l in (res.get("plainLyrics") or "").splitlines()]
        if not any(plain):
            return None
        return {"synced": False, "lines": [{"t": None, "text": l} for l in plain], "provider": "LRCLIB"}


class SponsorBlock:
    """Skip non-music parts of YouTube music videos (talking intros, skits,
    outros) using the community SponsorBlock database, like Spotube's
    "skip non-music segments" option."""

    API = "https://sponsor.ajay.app/api/skipSegments"

    def __init__(self, services: "Services"):
        self.svc = services

    async def segments(self, video_id: str, categories: list[str]) -> list[tuple[float, float]]:
        row = self.svc.db.one("SELECT * FROM skip_segments WHERE video_id=?", (video_id,))
        if row and time.time() - row["fetched_at"] < 3 * 86400:
            data = loads(row["segments"], [])
        else:
            try:
                r = await self.svc.http.get(self.API, params={
                    "videoID": video_id, "categories": dumps(["sponsor", "selfpromo", "interaction",
                                                              "intro", "outro", "music_offtopic"]),
                    "actionType": "skip"}, timeout=6)
            except httpx.HTTPError:
                return []
            if r.status_code == 404:
                data = []
            elif r.status_code == 200:
                data = [{"c": s.get("category"), "s": s["segment"][0], "e": s["segment"][1]} for s in r.json()]
            else:
                return []
            self.svc.db.execute("INSERT OR REPLACE INTO skip_segments(video_id, segments, fetched_at) VALUES (?,?,?)",
                                (video_id, dumps(data), time.time()))
        return sorted((d["s"], d["e"]) for d in data if d.get("c") in categories and d["e"] - d["s"] >= 1)
