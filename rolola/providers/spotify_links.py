"""Spotify links (metadata only).

Paste an ``open.spotify.com`` track, album or playlist link and the
rolola reads the song names from Spotify's public *embed* page, then plays
each song from your library / YouTube / SoundCloud through the matcher
(the Spotube approach).  No Spotify account is needed and no Spotify audio
is used, so the queue shows "via YouTube" (or similar) on every such track.

Limits: the embed page lists at most ~50-100 tracks of a playlist, it is an
undocumented page (may change without notice), and reading it is outside
Spotify's developer terms.  Real Spotify playback (librespot / Spotify
Connect, Premium required) is on the roadmap.
"""

from __future__ import annotations

import json
import re

import httpx

from ..models import Playable, Track
from .base import ConfigField, Provider, ProviderError

LINK_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album|playlist)/([A-Za-z0-9]{22})")
URI_RE = re.compile(r"^spotify:(track|album|playlist):([A-Za-z0-9]{22})$")
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def parse_link(text: str) -> tuple[str, str] | None:
    text = text.strip()
    m = LINK_RE.search(text) or URI_RE.match(text)
    return (m.group(1), m.group(2)) if m else None


def _image(entity: dict) -> str | None:
    for key in ("visualIdentity", "coverArt"):
        imgs = (entity.get(key) or {}).get("image") or (entity.get(key) or {}).get("sources") or []
        if imgs:
            return max(imgs, key=lambda i: i.get("maxWidth") or i.get("width") or 0).get("url")
    return None


class SpotifyLinksProvider(Provider):
    id = "spotify_links"
    name = "Spotify links"
    description = "Paste a Spotify track/album/playlist link; songs play from your other sources."
    account = "None (no Spotify audio is used)."
    legal = ("Reads public metadata from Spotify's embed pages (undocumented, outside Spotify's developer terms). "
             "Audio comes from your other enabled sources, so their risks apply.")
    risk = "medium"
    default_enabled = True
    capabilities = ("urls", "metadata_only")
    config_fields = [ConfigField("enabled", "Enabled", "bool", default=True)]

    def owns_url(self, url: str) -> bool:
        return parse_link(url) is not None

    async def _entity(self, kind: str, sid: str) -> dict:
        try:
            r = await self.svc.http.get(f"https://open.spotify.com/embed/{kind}/{sid}",
                                        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"}, timeout=10)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            err = ProviderError("Couldn't read that Spotify link.", status="offline")
            self.fail(err)
            raise err from exc
        m = NEXT_DATA.search(r.text)
        try:
            entity = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
        except (AttributeError, KeyError, TypeError, ValueError):
            err = ProviderError("Spotify changed its page format; this feature needs an update.", status="broken")
            self.fail(err)
            raise err
        self.ok()
        return entity

    async def search(self, query: str, limit: int = 20) -> list[Track]:
        link = parse_link(query)
        if not link:
            return []  # only links; Spotify search needs the official API (roadmap)
        kind, sid = link
        e = await self._entity(kind, sid)
        if kind == "track":
            return [self._track_from_entity(e, sid)]
        return [Track(source=self.id, id=f"{kind}:{sid}", kind="album" if kind == "album" else "playlist",
                      title=e.get("name") or e.get("title") or "Spotify " + kind, artist=e.get("subtitle"),
                      artwork=_image(e), count=len(e.get("trackList") or []) or None,
                      url=f"https://open.spotify.com/{kind}/{sid}")]

    def _track_from_entity(self, e: dict, sid: str) -> Track:
        artists = ", ".join(a.get("name", "") for a in e.get("artists") or []) or e.get("subtitle")
        return Track(source=self.id, id=sid, title=e.get("name") or e.get("title") or "Unknown", artist=artists,
                     duration=(e.get("duration") or 0) / 1000 or None, artwork=_image(e),
                     url=f"https://open.spotify.com/track/{sid}")

    async def expand(self, item: Track) -> list[Track]:
        if not item.is_container:
            return [item]
        kind, sid = item.id.split(":", 1)
        e = await self._entity(kind, sid)
        out = []
        for t in e.get("trackList") or []:
            uri = t.get("uri") or ""
            if not uri.startswith("spotify:track:"):
                continue
            out.append(Track(source=self.id, id=uri.rsplit(":", 1)[1], title=t.get("title") or "Unknown",
                             artist=t.get("subtitle"), duration=(t.get("duration") or 0) / 1000 or None,
                             album=item.title if kind == "album" else None,
                             artwork=item.artwork if kind == "album" else None,
                             url=f"https://open.spotify.com/track/{uri.rsplit(':', 1)[1]}"))
        return out

    async def resolve(self, track: Track) -> Playable:
        # Never reached: the player substitutes metadata-only tracks first.
        raise ProviderError("Spotify audio isn't available; enable YouTube or another source to play this.")

    async def test(self) -> str:
        e = await self._entity("track", "4uLU6hMCjMI75M1A2tKUQC")
        return f"OK ({e.get('name')})"