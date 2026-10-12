"""Sources played through yt-dlp: YouTube, SoundCloud, Bandcamp.

Audio is downloaded to the cache before it plays (see cache.py), so a slow
or flaky connection never interrupts a song.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..cache import cache_id_for
from ..models import Playable, Track
from ..ytdlp import YtdlpError
from .base import ConfigField, Provider, ProviderError

YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class YtdlpProvider(Provider):
    cookie_name = "generic"

    def cookies(self) -> str | None:
        return self.svc.ytdlp.cookie_file(self.cookie_name, self.config.get("cookies"))

    async def _json(self, target: str, flat: bool = True, **kw) -> dict:
        try:
            data = await self.svc.ytdlp.json(target, flat=flat, cookies=self.cookies(), **kw)
            self.ok()
            return data
        except YtdlpError as exc:
            self.fail(exc)
            raise ProviderError(str(exc), status=self.status) from exc

    def page_url(self, track: Track) -> str:
        return track.url or track.id

    async def resolve(self, track: Track) -> Playable:
        url = self.page_url(track)

        async def fetch(base: Path) -> Path:
            try:
                return await self.svc.ytdlp.download(url, base, cookies=self.cookies())
            except YtdlpError as exc:
                self.fail(exc)
                raise ProviderError(str(exc), status=self.status) from exc

        path = await self.svc.cache.ensure(track.key, fetch)
        self.ok()
        return Playable(target=str(path), cache_id=cache_id_for(track.key))

    def is_cached(self, track: Track) -> bool:
        return self.svc.cache.lookup(track.key) is not None


class YouTubeProvider(YtdlpProvider):
    id = "youtube"
    name = "YouTube"
    description = "Search YouTube and play the audio track (via yt-dlp)."
    account = "None for public videos. Optional cookies.txt from a signed-in browser helps with bot checks and age limits."
    legal = ("Downloading audio from YouTube violates YouTube's Terms of Service. Use at your own risk; the "
             "extractor can break whenever YouTube changes (update yt-dlp from the admin panel). YouTube Music "
             "Premium is not required and does not change this.")
    risk = "high"
    default_enabled = True
    cookie_name = "youtube"
    capabilities = ("search", "playlists", "similar", "urls")
    config_fields = [
        ConfigField("enabled", "Enabled", "bool", default=True),
        ConfigField("music_bias", "Prefer music results", "bool", default=True,
                    help="Ranks 'Topic'/'Official Audio' uploads higher."),
        ConfigField("cookies", "cookies.txt (Netscape format)", "textarea", default="",
                    help="Optional. Export from a browser signed in to a throwaway Google account. Stored encrypted."),
    ]

    def owns_url(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host.endswith(("youtube.com", "youtu.be", "youtube-nocookie.com"))

    def _entry(self, e: dict) -> Track | None:
        vid = e.get("id")
        # flat search results carry no duration for live streams and premieres
        if (not vid or not YT_ID.match(vid) or e.get("live_status") in ("is_live", "is_upcoming")
                or ("duration" in e and not e.get("duration"))):
            return None
        title = e.get("title") or vid
        channel = e.get("channel") or e.get("uploader") or ""
        artist = channel.removesuffix(" - Topic") or None
        return Track(
            source="youtube", id=vid, title=title, artist=artist, duration=e.get("duration"),
            artwork=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", url=f"https://www.youtube.com/watch?v={vid}",
        )

    @staticmethod
    def _music_score(t: Track) -> int:
        title = t.title.lower()
        s = 0
        if "official audio" in title or "(audio)" in title:
            s -= 2
        if t.artist and t.artist.lower() in title:
            s -= 1
        if any(w in title for w in ("reaction", "tutorial", "cover by", "karaoke", "review")):
            s += 3
        if t.duration and t.duration > 900:
            s += 2 if t.duration < 1800 else 6  # mixes and compilations last
        return s

    async def search(self, query: str, limit: int = 20) -> list[Track]:
        if self.owns_url(query):
            return await self._from_url(query)
        data = await self._json(f"ytsearch{min(limit, 25)}:{query}")
        tracks = [t for t in (self._entry(e) for e in data.get("entries") or []) if t]
        if self.config.get("music_bias", True):
            ranked = sorted(enumerate(tracks), key=lambda p: (self._music_score(p[1]), p[0]))
            tracks = [t for _, t in ranked]
        return tracks

    async def _from_url(self, url: str) -> list[Track]:
        data = await self._json(url)
        if data.get("_type") == "playlist":
            return [Track(source="youtube", id=data.get("webpage_url") or url, kind="playlist",
                          title=data.get("title") or "YouTube playlist", artist=data.get("uploader"),
                          count=len(data.get("entries") or []) or None, url=data.get("webpage_url") or url)]
        t = self._entry(data)
        return [t] if t else []

    async def expand(self, item: Track) -> list[Track]:
        if item.kind != "playlist":
            return [item]
        data = await self._json(item.id, extra=["--playlist-end", "200"])
        return [t for t in (self._entry(e) for e in data.get("entries") or []) if t]

    def page_url(self, track: Track) -> str:
        if not YT_ID.match(track.id):
            raise ProviderError("Invalid YouTube id.")
        return f"https://www.youtube.com/watch?v={track.id}"

    async def similar(self, track: Track, limit: int = 10) -> list[Track]:
        if track.source != "youtube":
            if not (track.artist or track.title):
                return []
            return (await self.search(f"{track.artist or ''} {track.title}", limit=limit))[1:]
        data = await self._json(f"https://www.youtube.com/watch?v={track.id}&list=RD{track.id}",
                                extra=["--playlist-end", str(limit + 1)])
        return [t for t in (self._entry(e) for e in data.get("entries") or []) if t and t.id != track.id][:limit]


class SoundCloudProvider(YtdlpProvider):
    id = "soundcloud"
    name = "SoundCloud"
    description = "Search public SoundCloud tracks (via yt-dlp)."
    account = "None. Go+ tracks only play a 30-second preview without a subscription (not supported)."
    legal = "Downloading via unofficial client violates SoundCloud's API terms; the official API is closed to new apps."
    risk = "medium"
    default_enabled = True
    cookie_name = "soundcloud"
    capabilities = ("search", "urls")
    config_fields = [ConfigField("enabled", "Enabled", "bool", default=True),
                     ConfigField("hide_previews", "Hide 30-second previews", "bool", default=True)]

    def owns_url(self, url: str) -> bool:
        return (urlparse(url).hostname or "").lower().endswith("soundcloud.com")

    def _entry(self, e: dict) -> Track | None:
        url = e.get("url") or e.get("webpage_url")
        if not url:
            return None
        preview = e.get("duration") == 30.0
        if preview and self.config.get("hide_previews", True):
            return None
        thumb = None
        if e.get("thumbnails"):
            thumb = e["thumbnails"][-1].get("url")
        return Track(source="soundcloud", id=str(e.get("id")), title=e.get("title") or "Untitled",
                     artist=e.get("uploader"), duration=e.get("duration"), artwork=thumb or e.get("thumbnail"),
                     url=url, extra={"preview": preview})

    async def search(self, query: str, limit: int = 20) -> list[Track]:
        if self.owns_url(query):
            data = await self._json(query)
            if data.get("_type") == "playlist":
                return [Track(source="soundcloud", id=data.get("webpage_url") or query, kind="playlist",
                              title=data.get("title") or "SoundCloud set", artist=data.get("uploader"),
                              url=data.get("webpage_url") or query)]
            t = self._entry({**data, "url": data.get("webpage_url") or query})
            return [t] if t else []
        data = await self._json(f"scsearch{min(limit, 25)}:{query}")
        return [t for t in (self._entry(e) for e in data.get("entries") or []) if t]

    async def expand(self, item: Track) -> list[Track]:
        if item.kind != "playlist":
            return [item]
        data = await self._json(item.id, extra=["--playlist-end", "200"])
        return [t for t in (self._entry(e) for e in data.get("entries") or []) if t]

    def page_url(self, track: Track) -> str:
        url = track.url or ""
        if not self.owns_url(url):
            raise ProviderError("Invalid SoundCloud link.")
        return url


class BandcampProvider(YtdlpProvider):
    id = "bandcamp"
    name = "Bandcamp"
    description = "Search Bandcamp and play tracks that artists stream for free."
    account = "None. Only tracks the artist allows to stream in full will play."
    legal = ("Uses Bandcamp's public website search and stream URLs (unofficial). Low risk, but consider buying music "
             "you play often to support the artists.")
    risk = "medium"
    default_enabled = True
    cookie_name = "bandcamp"
    capabilities = ("search", "albums", "urls")
    config_fields = [ConfigField("enabled", "Enabled", "bool", default=True)]

    def owns_url(self, url: str) -> bool:
        return (urlparse(url).hostname or "").lower().endswith("bandcamp.com")

    async def search(self, query: str, limit: int = 20) -> list[Track]:
        if self.owns_url(query):
            data = await self._json(query)
            if data.get("_type") == "playlist":
                return [Track(source="bandcamp", id=query, kind="album", title=data.get("title") or "Album",
                              artist=data.get("uploader") or data.get("artist"), url=query)]
            return [self._from_info(data, query)]
        out: list[Track] = []
        for flt in ("t", "a"):
            try:
                r = await self.svc.http.post(
                    "https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic",
                    json={"search_text": query, "search_filter": flt, "full_page": False, "fan_id": None},
                    timeout=10,
                )
                r.raise_for_status()
                results = r.json().get("auto", {}).get("results", [])
            except (httpx.HTTPError, ValueError) as exc:
                self.fail(ProviderError("Bandcamp search is unreachable.", status="offline"))
                raise ProviderError("Bandcamp search is unreachable.", status="offline") from exc
            for x in results:
                url = x.get("item_url_path")
                if not url:
                    continue
                kind = "track" if x.get("type") == "t" else "album"
                out.append(Track(source="bandcamp", id=url, kind=kind, title=x.get("name") or "Untitled",
                                 artist=x.get("band_name"), album=x.get("album_name"), artwork=x.get("img"), url=url))
        self.ok()
        return out[:limit]

    def _from_info(self, e: dict, url: str) -> Track:
        return Track(source="bandcamp", id=url, title=e.get("track") or e.get("title") or "Untitled",
                     artist=e.get("artist") or e.get("uploader"), album=e.get("album"), duration=e.get("duration"),
                     artwork=e.get("thumbnail"), url=url)

    async def expand(self, item: Track) -> list[Track]:
        if item.kind != "album":
            return [item]
        data = await self._json(item.id)
        tracks = []
        for e in data.get("entries") or []:
            url = e.get("url") or e.get("webpage_url")
            if url:
                tracks.append(Track(source="bandcamp", id=url, title=e.get("title") or "Untitled",
                                    artist=item.artist, album=item.title, duration=e.get("duration"),
                                    artwork=item.artwork, url=url))
        return tracks

    def page_url(self, track: Track) -> str:
        if not self.owns_url(track.id):
            raise ProviderError("Invalid Bandcamp link.")
        return track.id
