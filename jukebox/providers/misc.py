"""Podcasts (RSS), direct URLs, and honest placeholders for Spotify/Tidal."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

import httpx

from ..cache import cache_id_for
from ..models import Playable, Track
from ..net import check_public_url, resolve_stream_url
from ..ytdlp import YtdlpError
from .base import ConfigField, Provider, ProviderError

ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


def parse_duration(text: str | None) -> float | None:
    if not text:
        return None
    try:
        parts = [float(p) for p in text.strip().split(":")]
    except ValueError:
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total or None


class PodcastProvider(Provider):
    id = "podcast"
    name = "Podcasts"
    description = "Find podcasts (Apple directory) and play episodes from their public RSS feeds."
    account = "None."
    legal = "Podcast feeds are published for free listening. Low risk."
    risk = "low"
    default_enabled = True
    capabilities = ("search", "browse")
    config_fields = [ConfigField("enabled", "Enabled", "bool", default=True)]

    async def search(self, query: str, limit: int = 20) -> list[Track]:
        if query.startswith(("http://", "https://")) and re.search(r"(rss|feed|\.xml)", query, re.I):
            return [Track(source=self.id, id=query, kind="podcast", title="Podcast feed", url=query)]
        try:
            r = await self.svc.http.get("https://itunes.apple.com/search",
                                        params={"media": "podcast", "term": query, "limit": min(limit, 15)}, timeout=10)
            r.raise_for_status()
            results = r.json().get("results", [])
        except (httpx.HTTPError, ValueError) as exc:
            err = ProviderError("The podcast directory is unreachable.", status="offline")
            self.fail(err)
            raise err from exc
        self.ok()
        return [Track(source=self.id, id=x["feedUrl"], kind="podcast", title=x.get("collectionName") or "Podcast",
                      artist=x.get("artistName"), artwork=x.get("artworkUrl600") or x.get("artworkUrl100"),
                      count=x.get("trackCount"), url=x.get("collectionViewUrl"))
                for x in results if x.get("feedUrl")]

    async def expand(self, item: Track) -> list[Track]:
        if item.kind != "podcast":
            return [item]
        await check_public_url(item.id, self.svc.settings.get("allow_private_urls"))
        try:
            r = await self.svc.http.get(item.id, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content[:10_000_000])
        except (httpx.HTTPError, ET.ParseError) as exc:
            raise ProviderError("Couldn't read the podcast feed.") from exc
        channel = root.find("channel")
        if channel is None:
            raise ProviderError("That isn't an RSS podcast feed.")
        show = channel.findtext("title") or item.title
        image = (channel.find(f"{ITUNES_NS}image").get("href") if channel.find(f"{ITUNES_NS}image") is not None
                 else item.artwork)
        out = []
        for it in channel.findall("item")[:60]:
            enc = it.find("enclosure")
            url = enc.get("url") if enc is not None else None
            if not url or not (enc.get("type") or "audio").startswith("audio"):
                continue
            ep_img = it.find(f"{ITUNES_NS}image")
            out.append(Track(source=self.id, id=url, kind="episode", title=(it.findtext("title") or "Episode").strip(),
                             artist=show, duration=parse_duration(it.findtext(f"{ITUNES_NS}duration")),
                             artwork=ep_img.get("href") if ep_img is not None else image,
                             extra={"published": it.findtext("pubDate")}))
        return out

    async def resolve(self, track: Track) -> Playable:
        await check_public_url(track.id, self.svc.settings.get("allow_private_urls"))
        return Playable(target=track.id)


class UrlProvider(Provider):
    id = "url"
    name = "Direct link"
    description = "Play any audio file or stream URL, or a page yt-dlp understands (1000+ sites)."
    account = "None."
    legal = "Depends on the link. Users are responsible for what they paste."
    risk = "medium"
    default_enabled = True
    capabilities = ("urls",)
    config_fields = [ConfigField("enabled", "Enabled", "bool", default=True),
                     ConfigField("generic_extractor", "Use yt-dlp for web pages", "bool", default=True)]

    async def search(self, query: str, limit: int = 20) -> list[Track]:
        if not query.startswith(("http://", "https://")):
            return []
        url = await check_public_url(query, self.svc.settings.get("allow_private_urls"))
        live, ctype = False, ""
        try:
            async with self.svc.http.stream("GET", url, timeout=8, headers={"Icy-MetaData": "1"}) as r:
                ctype = r.headers.get("content-type", "").lower()
                live = "icy-name" in r.headers or "icy-metaint" in r.headers
                name = r.headers.get("icy-name")
        except httpx.HTTPError as exc:
            raise ProviderError("Couldn't open that link.") from exc
        title = name or unquote(PurePosixPath(urlparse(url).path).name) or url
        if ctype.startswith("audio/") or ctype in ("application/ogg", "application/x-mpegurl",
                                                   "application/vnd.apple.mpegurl", "audio/x-scpls") or live:
            return [Track(source=self.id, id=url, title=title[:200], live=live or ctype.endswith(("mpegurl", "scpls")),
                          kind="station" if live else "track", url=url, extra={"mode": "direct"})]
        if not self.config.get("generic_extractor", True):
            raise ProviderError("That link isn't an audio file.")
        try:
            data = await self.svc.ytdlp.json(url, flat=True)
        except YtdlpError as exc:
            raise ProviderError(str(exc)) from exc
        return [Track(source=self.id, id=url, title=data.get("title") or title, artist=data.get("uploader"),
                      duration=data.get("duration"), artwork=data.get("thumbnail"), url=url, extra={"mode": "ytdlp"})]

    async def resolve(self, track: Track) -> Playable:
        url = await check_public_url(track.id, self.svc.settings.get("allow_private_urls"))
        if track.extra.get("mode") == "ytdlp":
            path = await self.svc.cache.ensure(track.key, lambda base: self.svc.ytdlp.download(url, base))
            return Playable(target=str(path), cache_id=cache_id_for(track.key))
        return Playable(target=await resolve_stream_url(self.svc.http, url), live=track.live)

    async def test(self) -> str:
        return "OK"


class PlannedProvider(Provider):
    """Shown in the admin panel so the status of premium services is explicit."""
    capabilities = ()
    config_fields = []

    def current_status(self):
        return "planned", self.description

    @property
    def enabled(self) -> bool:
        return False

    async def search(self, query: str, limit: int = 20) -> list[Track]:
        return []

    async def test(self) -> str:
        raise ProviderError("Not implemented yet (see ROADMAP.md).", status="planned")


class SpotifyPlanned(PlannedProvider):
    id = "spotify"
    name = "Spotify (native)"
    description = "Planned: act as a Spotify Connect speaker via librespot. Spotify links already work through matching."
    account = "Spotify Premium."
    legal = ("librespot is an unofficial client; using it breaks Spotify's terms. Spotify's official Web API can only "
             "control playback on official devices, not stream audio to a server.")
    risk = "high"


class TidalPlanned(PlannedProvider):
    id = "tidal"
    name = "Tidal"
    description = "Planned: unofficial tidalapi with a paid subscription (fragile)."
    account = "A paid Tidal subscription."
    legal = "No public streaming API; unofficial access breaks Tidal's terms and can lose the account."
    risk = "high"
