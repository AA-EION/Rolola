# Architecture

## Stack and why

| Part | Choice | Why |
|---|---|---|
| Language / web | Python 3.13, FastAPI + Starlette, uvicorn | Boring and well known. One asyncio process easily serves 50 phones; the heavy lifting (decoding) is not in Python. |
| Audio engine | **mpv** controlled over its JSON IPC socket | Battle-tested decoder for every format and stream, gapless playback, ALSA output, per-file gain, ICY metadata, network cache. No PulseAudio/PipeWire needed. |
| Extractors | **yt-dlp** (+ Deno for YouTube's JS challenges) as a niced subprocess | The most actively maintained extractor; runs out of process so a hang or crash never touches playback; updatable inside the volume. |
| Loudness | ffmpeg `ebur128` (EBU R128) | Measures each cached/local file once; mpv applies the gain. |
| Storage | SQLite (WAL) in `/data` | Zero-maintenance, crash-safe, snapshot-friendly. FTS5 indexes the local library. |
| Live sync | WebSockets, server-push of full state (≤10/s, coalesced) | Simple and robust: clients never drift because they always get the whole truth. |
| Frontend | Plain ES modules, no build step, PWA | Nothing to compile, nothing to rot; ~60 KB total. |
| Packaging | One Debian-slim image, tini as PID 1, non-root (568), multi-arch base images | One container as required; Debian ships mpv/ffmpeg with security updates. |

## Processes inside the container

```
tini ─ python -m rolola (uvicorn, 1 process, asyncio)
         ├─ mpv --idle --ao=alsa --audio-device=alsa/$AUDIO_DEVICE   (supervised, restarted with back-off)
         ├─ yt-dlp / ffmpeg subprocesses                                (nice 10, on demand)
         └─ library scanner thread                                      (thread nice 15)
```

**Audio has priority**: mpv keeps a 1 s output buffer and plays only *local files* for remote sources
(they're downloaded to the cache before they start), so network hiccups can't cause dropouts.
Everything else runs at lower CPU priority. With `cap_add: SYS_NICE`, mpv itself gets nice −10.

## Module map

```
rolola/
  app.py        HTTP/WS API, auth dependencies, static files
  player.py     playback controller (state machine around mpv)
  engine.py     mpv process supervision + JSON IPC client; ALSA diagnostics
  queue.py      queue storage and the fair ordering algorithm (pure function order_items)
  votes.py      vote-skip thresholds (pure)
  matcher.py    cross-source matching (Spotify links → YouTube/library), ranking, cached choices
  cache.py      audio download cache (LRU) + loudness analysis
  ytdlp.py      yt-dlp subprocess wrapper, friendly errors, self-update/rollback
  lyrics.py     LRCLIB synced lyrics + SponsorBlock segments
  rules.py      blocklist + quiet-hours schedule
  auth.py       guest sessions, proxy identity, API tokens, admin password (scrypt)
  proxy.py      ASGI middleware: X-Forwarded-*, BASE_PATH, trusted-peer tracking
  settings.py   runtime settings (DB > env default > built-in), encrypted secrets (Fernet)
  hub.py        WebSocket fan-out with per-client bounded queues
  providers/    one module per source (see below)
  web/          the PWA
```

## Playback state machine

* The queue lives in SQLite; `order_items()` computes the play order on demand (fair round-robin, pins first).
* A **prefetcher** prepares the next *N* tracks: resolve the source, match if needed, download to
  the cache, measure loudness. Failures are logged quietly and retried when the track is due.
* When the next track is ready, it's **appended to mpv's playlist** with its gain, so mpv starts it
  gaplessly. A queue change that alters "next" clears and re-appends it.
* mpv events drive transitions: `start-file` of the preloaded entry → history row, votes reset,
  fairness bookkeeping; `end-file` → next.
* A failing track is recorded in history with the reason and announced in a toast ("Couldn't play X:
  This video is unavailable. Skipping."). Five failures in a row pause for 20 s, so a dead network
  doesn't burn through the queue.
* **Sound-card failure** (`audio output initialization failed`) is different: the track is kept,
  a banner tells everyone, and playback retries every 10 s until the device is back.
* If **mpv dies**, the supervisor restarts it and the current track resumes at its last position.
* Every 5 s the current track and position are saved, so **container restarts and image updates resume**.
* Autoplay keeps a small buffer of picks (YouTube mix of recent tracks, office history, library
  shuffle, a playlist or a station). A user's track interrupts autoplay immediately (configurable).
* Quiet hours pause at the window's end and resume at its start.

## Identity and security

* **Guests** choose a name; the server creates a user and an HttpOnly, SameSite=Lax session cookie
  (path = public prefix). Mutating requests must carry `X-Rolola: 1`, which a cross-site form can't
  send without a CORS preflight, and we never allow one. WebSockets check `Origin` against `Host`.
* **Proxy SSO**: `AUTH_HEADER` is trusted only when the TCP peer is in `TRUSTED_PROXIES`.
* **Admin**: scrypt-hashed password, proxy users/groups, an `admin` role, or an admin API token.
* **Rate limits** (token buckets per IP/user): joins, admin login, searches, writes.
* **SSRF**: user-supplied URLs (direct links, podcast feeds) can't point at private/loopback
  addresses unless the admin allows it. YouTube/SoundCloud/Bandcamp are only fetched by
  canonical ids/hosts, never by client-provided stream URLs.
* **Secrets**: provider credentials are Fernet-encrypted in the DB, returned to the UI as `***`,
  and registered with the log redactor so they never appear in logs.
* Validated input everywhere (pydantic models with size limits); security headers; non-root container.

## The provider plugin interface

```python
class Provider:
    id = "mysource"; name = "My Source"; description = "…"
    account = "What account is needed"; legal = "ToS notes"; risk = "low|medium|high"
    capabilities = ("search", "albums", "similar", "random", "urls", "metadata_only", "live")
    config_fields = [ConfigField("enabled", "Enabled", "bool", default=True),
                     ConfigField("api_key", "API key", "secret")]

    async def search(self, query, limit) -> list[Track]        # tracks and containers
    async def expand(self, item) -> list[Track]                # album/playlist/podcast → tracks
    async def resolve(self, track) -> Playable                 # path or URL for mpv (may download via svc.cache)
    async def similar(self, track, limit) -> list[Track]       # optional, for autoplay
    async def random_tracks(self, limit) -> list[Track]        # optional, for autoplay
    async def test(self) -> str                                # admin "Test" button
    def owns_url(self, url) -> bool                            # paste-a-link routing
    def configured(self) -> bool                               # False → "needs setup"
```

* Register the class in `providers/__init__.py`. The admin panel builds its form from
  `config_fields`, stores `secret` fields encrypted, and shows `account`/`legal`/`risk`.
* Call `self.ok()` / `self.fail(exc)` to drive the live status (connected / offline / rate-limited / broken).
* Raise `ProviderError("plain-language message")`: that text is what users see.
* A **metadata-only** provider (capability `metadata_only`) never plays audio itself: the player
  hands its tracks to the matcher, which finds the best equivalent on the audio sources and marks it
  `substituted_from` so the UI shows "Playing from YouTube (you picked it on Spotify) · Wrong song?".
* `Track` fields: `source, id, kind, title, artist, album, duration, artwork, url, live, count, extra`.
  Keys in `extra` starting with `_` stay server-side.

## Known limitations

* Single process by design (state lives in memory + SQLite); it's meant for one office and one sound card.
* Live sync pushes the full state; with very large queues (≥1000 items) only the first 300 are sent.
* Tracks are downloaded before playing; a 15-minute YouTube track typically takes 2–6 s, longer on slow
  links. The UI shows "Getting it ready…". Prefetching hides this for everything after the first track.
* Gapless is "weak" gapless (mpv): perfect between files with the same sample format, a few ms otherwise.
* Per-source limitations are in [SOURCES.md](SOURCES.md).
