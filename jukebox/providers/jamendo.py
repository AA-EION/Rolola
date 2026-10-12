"""Jamendo: 500k+ Creative Commons tracks through the official API."""

from __future__ import annotations

import random
from pathlib import Path

import httpx

from ..cache import cache_id_for
from ..models import Playable, Track
from .base import ConfigField, Provider, ProviderError

API = "https://api.jamendo.com/v3.0"


async def http_download(client: httpx.AsyncClient, url: str, base: Path, ext: str = "mp3", max_mb: int = 300) -> Path:
    final = base.with_name(base.name + "." + ext)
    tmp = base.with_name(base.name + ".part")
    size = 0
    async with client.stream("GET", url, timeout=httpx.Timeout(20, read=60), follow_redirects=True) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            async for chunk in r.aiter_bytes(65536):
                size += len(chunk)
                if size > max_mb * 1024 * 1024:
                    raise ProviderError("File too large.")
                fh.write(chunk)
    tmp.rename(final)
    return final


class JamendoProvider(Provider):
    id = "jamendo"
    name = "Jamendo"
    description = "Free Creative Commons music (official API)."
    account = "A free Jamendo developer client ID from https://devportal.jamendo.com."
    legal = ("Official API, CC-licensed music, non-commercial terms. Background music in a business may need a "
             "Jamendo Licensing plan; personal/office listening of CC tracks is generally fine.")
    risk = "low"
    capabilities = ("search", "albums", "random")
    config_fields = [
        ConfigField("enabled", "Enabled", "bool", default=False),
        ConfigField("client_id", "Client ID", "secret", default="", help="Create an app at devportal.jamendo.com."),
    ]

    def configured(self) -> bool:
        return bool(self.config.get("client_id"))

    async def _get(self, path: str, **params) -> list[dict]:
        if not self.configured():
            raise ProviderError("Jamendo needs a client ID (admin → Sources).", status="needs_config")
        params = {"client_id": self.config["client_id"], "format": "json", **params}
        try:
            r = await self.svc.http.get(f"{API}{path}", params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            err = ProviderError("Jamendo is unreachable.", status="offline")
            self.fail(err)
            raise err from exc
        head = data.get("headers", {})
        if head.get("status") != "success":
            err = ProviderError(f"Jamendo error: {head.get('error_message') or head.get('code')}", status="broken")
            self.fail(err)
            raise err
        self.ok()
        return data.get("results", [])

    def _track(self, x: dict) -> Track:
        return Track(source="jamendo", id=str(x["id"]), title=x.get("name") or "Untitled", artist=x.get("artist_name"),
                     album=x.get("album_name"), duration=x.get("duration"), artwork=x.get("image") or x.get("album_image"),
                     url=x.get("shareurl"), extra={"license": x.get("license_ccurl")})

    async def search(self, query: str, limit: int = 20) -> list[Track]:
        tracks = await self._get("/tracks/", search=query, limit=limit, audioformat="mp32", order="relevance")
        albums = await self._get("/albums/", namesearch=query, limit=4)
        out = [Track(source="jamendo", id=f"album:{a['id']}", kind="album", title=a.get("name") or "Album",
                     artist=a.get("artist_name"), artwork=a.get("image")) for a in albums]
        return out + [self._track(t) for t in tracks]

    async def expand(self, item: Track) -> list[Track]:
        if item.kind != "album":
            return [item]
        rows = await self._get("/tracks/", album_id=item.id.split(":", 1)[1], limit=100, order="position")
        return [self._track(t) for t in rows]

    async def resolve(self, track: Track) -> Playable:
        rows = await self._get("/tracks/", id=track.id, audioformat="mp32")
        if not rows or not rows[0].get("audio"):
            raise ProviderError("Jamendo has no stream for this track.")
        url = rows[0]["audio"]
        path = await self.svc.cache.ensure(track.key, lambda base: http_download(self.svc.http, url, base))
        return Playable(target=str(path), cache_id=cache_id_for(track.key))

    async def random_tracks(self, limit: int = 10) -> list[Track]:
        rows = await self._get("/tracks/", limit=limit, order="popularity_week", audioformat="mp32",
                               offset=random.randint(0, 200))
        return [self._track(t) for t in rows]

    async def test(self) -> str:
        await self._get("/tracks/", limit=1)
        return "OK"
