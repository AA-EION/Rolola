"""Internet radio: the community-run radio-browser.info directory plus
custom stations added by an admin (those work without the directory)."""

from __future__ import annotations

import random

import httpx

from ..logs import get_logger
from ..models import Playable, Track
from ..net import resolve_stream_url
from .base import ConfigField, Provider, ProviderError

log = get_logger("radio")
FALLBACK_SERVERS = ["de1.api.radio-browser.info", "de2.api.radio-browser.info", "fi1.api.radio-browser.info"]


class RadioProvider(Provider):
    id = "radio"
    name = "Internet radio"
    description = "Thousands of stations from radio-browser.info, plus your own stream URLs."
    account = "None. radio-browser.info is a free community directory."
    legal = ("Stations are public streams. Playing broadcast radio in a business may require a public-performance "
             "licence in some countries.")
    risk = "low"
    default_enabled = True
    capabilities = ("search", "live")
    config_fields = [
        ConfigField("enabled", "Enabled", "bool", default=True),
        ConfigField("directory", "Search radio-browser.info", "bool", default=True,
                    help="Disable to only offer custom stations."),
        ConfigField("country", "Prefer country code", "text", default="",
                    help="Optional ISO code (e.g. ES, US) to rank local stations first."),
    ]

    def __init__(self, services):
        super().__init__(services)
        self._servers: list[str] = []

    async def _api(self, path: str, params: dict | None = None):
        if not self._servers:
            try:
                r = await self.svc.http.get("https://all.api.radio-browser.info/json/servers", timeout=6)
                self._servers = sorted({s["name"] for s in r.json() if s.get("name")}) or FALLBACK_SERVERS[:]
            except (httpx.HTTPError, ValueError):
                self._servers = FALLBACK_SERVERS[:]
            random.shuffle(self._servers)
        last: Exception | None = None
        for server in list(self._servers)[:3]:
            try:
                r = await self.svc.http.get(f"https://{server}{path}", params=params, timeout=8)
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                self._servers.remove(server)
                self._servers.append(server)
        raise ProviderError("The radio directory is unreachable right now.", status="offline") from last

    def _station(self, s: dict) -> Track:
        tags = [t for t in (s.get("tags") or "").split(",") if t][:4]
        meta = " · ".join(x for x in [s.get("countrycode"), ", ".join(tags), (f"{s['bitrate']} kbps" if s.get("bitrate") else None)] if x)
        return Track(
            source="radio", id="rb:" + s["stationuuid"], kind="station", title=(s.get("name") or "Station").strip()[:200],
            artist=meta or None, artwork=s.get("favicon") or None, url=s.get("homepage") or None, live=True,
        )

    def _custom(self) -> list[dict]:
        return self.svc.db.all("SELECT * FROM stations ORDER BY name")

    def _custom_track(self, r: dict) -> Track:
        return Track(source="radio", id=f"custom:{r['id']}", kind="station", title=r["name"], artist=r.get("tags") or None,
                     artwork=r.get("favicon") or None, live=True)

    async def search(self, query: str, limit: int = 20) -> list[Track]:
        ql = query.lower()
        out = [self._custom_track(r) for r in self._custom()
               if ql in r["name"].lower() or ql in (r.get("tags") or "").lower()]
        if not self.config.get("directory", True):
            return out[:limit]
        try:
            common = {"limit": limit, "hidebroken": "true", "order": "clickcount", "reverse": "true"}
            by_name = await self._api("/json/stations/search", {**common, "name": query})
            by_tag = await self._api("/json/stations/search", {**common, "tag": query, "limit": max(5, limit // 2)})
            self.ok()
        except ProviderError:
            if out:
                return out
            raise
        seen: set[str] = set()
        stations = []
        country = (self.config.get("country") or "").upper()
        merged = by_name + by_tag
        if country:
            merged.sort(key=lambda s: 0 if s.get("countrycode") == country else 1)
        for s in merged:
            if s.get("stationuuid") in seen:
                continue
            seen.add(s["stationuuid"])
            stations.append(self._station(s))
        return (out + stations)[:limit]

    async def resolve(self, track: Track) -> Playable:
        if track.id.startswith("custom:"):
            row = self.svc.db.one("SELECT * FROM stations WHERE id=?", (int(track.id.split(":", 1)[1]),))
            if not row:
                raise ProviderError("This custom station was removed.")
            url = row["url"]
        elif track.id.startswith("rb:"):
            uuid = track.id[3:]
            data = await self._api(f"/json/url/{uuid}")  # also counts the click, as the directory asks
            url = (data or {}).get("url")
            if not url:
                st = await self._api(f"/json/stations/byuuid/{uuid}")
                url = st[0].get("url_resolved") if st else None
            if not url:
                raise ProviderError("The station has no working stream.")
        else:
            raise ProviderError("Unknown station.")
        url = await resolve_stream_url(self.svc.http, url)
        return Playable(target=url, live=True)

    async def test(self) -> str:
        if not self.config.get("directory", True):
            return f"{len(self._custom())} custom stations"
        data = await self._api("/json/stats")
        return f"Directory online: {data.get('stations')} stations"
