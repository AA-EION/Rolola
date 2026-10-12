# Music sources: what's possible, what it needs, what it risks

Status as of October 2026. "ToS risk" is the risk of breaking the service's terms, not legal advice.
Separately from any source, **playing music in a workplace can require a public-performance licence**
from your country's collecting society. Even Spotify/YouTube Premium personal plans don't cover it.

| Source | Status | Account | ToS risk | Notes |
|---|---|---|---|---|
| YouTube / YouTube Music | ✅ main source | none (cookies optional) | **High** | via yt-dlp + Deno |
| Local library | ✅ | none | low | offline, FTS search, covers |
| Internet radio | ✅ | none | low | radio-browser.info + custom URLs |
| SoundCloud | ✅ | none | medium | public tracks only |
| Bandcamp | ✅ | none | medium | free streams only |
| Podcasts | ✅ | none | low | Apple directory + RSS |
| Direct links | ✅ | none | depends | audio files, streams, yt-dlp pages |
| Spotify links | ✅ (metadata) | none | medium | audio from other sources |
| Jamendo | ✅ | free API key | low | Creative Commons |
| Spotify native | ❌ planned | Premium | high | librespot / Spotify Connect |
| Tidal | ❌ planned | paid plan | high | unofficial only |

## YouTube / YouTube Music: the main source

* **How**: `yt-dlp` searches (`ytsearch`), ranks music-like results first ("Official Audio",
  "- Topic" channels; reactions, karaoke and multi-hour compilations last), then downloads the best
  audio-only format (Opus/AAC, ~128–160 kbps) into the cache **before** it plays. Autoplay uses
  YouTube's own "Mix" (RD) playlists for "similar songs". SponsorBlock skips non-music segments.
* **Reliability**: good, with regular breakages when YouTube changes its player. We plan for it:
  * the extractor runs in a subprocess and updates **daily into the data volume**
    (`YTDLP_AUTO_UPDATE`), plus a one-click *Update yt-dlp now* and a rollback to the image version;
  * Deno is in the image for YouTube's JavaScript challenges;
  * errors are translated ("asks to confirm you're not a bot", "video unavailable", "rate-limited").
  * From datacenter IPs YouTube often demands sign-in ("not a bot"). From an office connection it
    usually doesn't. If it does, add cookies from a **throwaway** account.
* **YouTube Data API (official)**: offers search and metadata only, never audio, and a 10 000 units/day
  quota (≈100 searches). Not useful for playback.
* **ToS**: downloading YouTube content without YouTube's permission violates its Terms of Service.
  Account bans are possible if you use cookies; IP rate limits are the usual consequence otherwise.
  YouTube Music Premium does not authorise this either.

## Spotify

* **Implemented: Spotify links (metadata only).** Paste a track, album or playlist link. The names come from
  Spotify's public embed page, and each song is matched to your library first, then YouTube/SoundCloud
  (ranking: title, artist, "official" markers, **duration within a few seconds**). The choice is remembered
  and anyone can fix a wrong match. The UI always shows "Playing from YouTube (you picked it on Spotify)".
  Limits: the embed page lists ~50–100 tracks of a playlist; it's an undocumented page that may change.
* **Native Spotify audio (planned, phase 2)**: the only realistic way to get Spotify audio out of a
  server is to make it a **Spotify Connect speaker** with [librespot](https://github.com/librespot-org/librespot)
  (open-source, Rust), outputting to the same ALSA device. Requirements and limits:
  * **Spotify Premium** is mandatory (librespot refuses free accounts).
  * People "cast" to the jukebox from their own Spotify app. That doesn't merge with our fair queue,
    so the plan is an exclusive "Spotify mode" an admin switches on, or driving librespot through
    the Web API with one office account.
  * The **official Web API** can search and *control* playback on Spotify devices, but never deliver
    audio to our server. Since 2024–2025 new apps get very limited Web API access (development mode,
    25 users, Premium required).
  * librespot is an unofficial client; using it breaks Spotify's terms (low practical risk for a
    single Premium account, but real). Spotify's terms also forbid commercial/public use of personal plans.
* Not adopted: logging in with web-player tokens (Spotube's old approach). It breaks constantly
  (TOTP changes, 429s) and clearly violates the terms.

## Tidal (planned, phase 3)

* No public streaming API. The unofficial [`tidalapi`](https://github.com/tamland/python-tidal)
  (device-code login with a paid subscription) can search and return stream URLs, including lossless
  and some DRM-free formats. It's fragile (Tidal changes keys and auth) and breaks Tidal's terms;
  the account can be suspended.
* Fallback today: paste the song name, or use Spotify links. Matching then plays it from YouTube or your library.

## Local library ✅

Mount any folder at `/music` (read-only). An incremental scan (mtime/size) reads tags with mutagen
(MP3, FLAC, Ogg/Opus, M4A/AAC, WAV, AIFF, WMA, APE, WavPack, Musepack), indexes them in SQLite FTS5
(accent-insensitive), groups albums, and serves covers from `cover.jpg`/`folder.jpg` or embedded art.
ReplayGain tags are honoured; untagged files are measured. **Works offline.** No ToS risk.

## Internet radio ✅

[radio-browser.info](https://www.radio-browser.info) (free community directory, ~50 000 stations,
round-robin across its mirrors, click-counting as it requests) plus **custom stations** an admin
adds (these work even when the directory is down). `.pls`/`.m3u` are unwrapped; HLS works.
ICY "now playing" titles show in the UI. Usable as the autoplay fallback.

## SoundCloud ✅

yt-dlp's SoundCloud support (search + links). Public tracks play in full; "Go+" tracks are
30-second previews and are hidden. The official API has been closed to new apps for years, so this is
unofficial (medium risk). Reliability is good.

## Bandcamp ✅

Search uses Bandcamp's public website search endpoint; playback uses yt-dlp's Bandcamp extractor on
track/album pages. Only what artists allow to stream for free plays in full. Unofficial but gentle
(medium-low risk). Consider buying what the office loves.

## Jamendo ✅ (needs a free key)

Official API, ~600 000 Creative Commons tracks, albums, popularity charts (used for autoplay).
Needs a free *client ID* from devportal.jamendo.com. CC "non-commercial" terms: background music in a
business may need a Jamendo Licensing plan.

## Podcasts ✅

Search via the free Apple Podcasts directory API, or paste any RSS feed. Episodes stream directly
(not cached, since they're long). Low risk.

## Direct links ✅

Any `http(s)` audio file or stream (MP3/AAC/Ogg/Icecast/HLS), or any web page among yt-dlp's 1 000+
supported sites (Vimeo, Mixcloud, archive.org, …). LAN addresses are blocked unless the admin allows them.

## Other free sources worth adding (roadmap)

* **Free Music Archive / archive.org audio collections**: CC and public-domain music (FMA's API is
  unreliable since 2022; archive.org's advanced search API works well).
* **ccMixter / Musopen**: CC remixes and public-domain classical.
* **Navidrome / Jellyfin / Plex (Subsonic API)**: if the office already runs a media server.
* **MusicBrainz + Cover Art Archive**: better tags and covers (metadata only).
