"""Wires all components together (a tiny service container)."""

from __future__ import annotations

import httpx

from .auth import Auth
from .cache import AudioCache
from .config import Config
from .db import Database
from .hub import Hub
from .lyrics import Lyrics, SponsorBlock
from .matcher import Matcher
from .net import USER_AGENT
from .providers import PROVIDER_CLASSES, Provider
from .ratelimit import RateLimiter
from .rules import Blocklist
from .settings import Secrets, Settings
from .ytdlp import Ytdlp


class Services:
    def __init__(self, config: Config):
        self.config = config
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.cache_dir.mkdir(parents=True, exist_ok=True)
        config.run_dir.mkdir(parents=True, exist_ok=True)
        legacy = config.data_dir / "jukebox.db"  # installs from before the rename to Rolola
        if legacy.exists() and not config.db_path.exists():
            for suffix in ("", "-wal", "-shm"):
                old = config.data_dir / f"jukebox.db{suffix}"
                if old.exists():
                    old.rename(config.data_dir / f"{config.db_path.name}{suffix}")
        self.db = Database(config.db_path)
        self.settings = Settings(self.db, config.defaults, Secrets(config.data_dir, config.secret_key))
        self.auth = Auth(config, self.db, self.settings)
        self.http = httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True,
                                      timeout=httpx.Timeout(10.0), limits=httpx.Limits(max_connections=40))
        self.cache = AudioCache(config.cache_dir, self.db, config.cache_max_mb)
        self.ytdlp = Ytdlp(config.data_dir)
        self.hub = Hub()
        self.ratelimit = RateLimiter()
        self.blocklist = Blocklist(self.db)
        self.matcher = Matcher(self)
        self.lyrics = Lyrics(self)
        self.sponsorblock = SponsorBlock(self)
        self.providers: dict[str, Provider] = {cls.id: cls(self) for cls in PROVIDER_CLASSES}
        from .player import Player  # circular-import safe
        self.player = Player(self)

    def provider(self, pid: str) -> Provider | None:
        return self.providers.get(pid)

    def enabled_providers(self) -> list[Provider]:
        return [p for p in self.providers.values() if p.enabled]

    async def close(self) -> None:
        await self.http.aclose()
        self.db.close()
