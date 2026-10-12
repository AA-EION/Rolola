"""Shared data shapes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Kind = Literal["track", "album", "playlist", "station", "podcast", "episode", "artist"]


class Track(BaseModel):
    """A playable item, or a container (album/playlist/podcast) that a
    provider can expand into tracks. Providers fill what they know."""

    source: str = Field(max_length=32)
    id: str = Field(max_length=2048)
    kind: Kind = "track"
    title: str = Field(max_length=500)
    artist: str | None = Field(default=None, max_length=500)
    album: str | None = Field(default=None, max_length=500)
    duration: float | None = None
    artwork: str | None = Field(default=None, max_length=2048)
    url: str | None = Field(default=None, max_length=2048)  # human-facing page
    live: bool = False
    count: int | None = None  # number of tracks for containers
    extra: dict[str, Any] = Field(default_factory=dict)
    # Set when the track was substituted from another source
    substituted_from: dict[str, Any] | None = None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def is_container(self) -> bool:
        return self.kind in ("album", "playlist", "podcast", "artist")

    def public(self) -> dict:
        d = self.model_dump()
        # provider internals (e.g. resolved stream URLs) stay server-side
        d["extra"] = {k: v for k, v in self.extra.items() if not k.startswith("_")}
        d["key"] = self.key
        return d


class Playable(BaseModel):
    """What the audio engine actually opens."""

    target: str  # local path or URL
    headers: dict[str, str] = Field(default_factory=dict)
    live: bool = False
    cache_id: str | None = None  # for loudness lookups
    gain_db: float | None = None
