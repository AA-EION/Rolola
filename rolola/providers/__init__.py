"""Provider registry. Add new sources here (order = default search order)."""

from __future__ import annotations

from .base import Provider, ProviderError
from .jamendo import JamendoProvider
from .local import LocalProvider
from .misc import PodcastProvider, SpotifyPlanned, TidalPlanned, UrlProvider
from .radio import RadioProvider
from .spotify_links import SpotifyLinksProvider
from .ytdl_based import BandcampProvider, SoundCloudProvider, YouTubeProvider

# YouTube is the primary source; the local library keeps working offline.
PROVIDER_CLASSES: list[type[Provider]] = [
    YouTubeProvider,
    LocalProvider,
    SoundCloudProvider,
    RadioProvider,
    BandcampProvider,
    JamendoProvider,
    PodcastProvider,
    SpotifyLinksProvider,
    UrlProvider,
    SpotifyPlanned,
    TidalPlanned,
]

__all__ = ["PROVIDER_CLASSES", "Provider", "ProviderError"]
