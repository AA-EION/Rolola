# Roadmap

## ✅ Phase 1: MVP (this release, 1.0.0)

- Single container, ALSA output via mpv, device by name, live device switching, test tone, diagnostics.
- Shared queue with fair round-robin, per-user limit, pins, own/admin reorder and remove.
- Vote-skip (percent or count), owner instant skip, admin force skip, likes, favourites, playlists
  (personal and shared), history, personal stats.
- Sources: **YouTube (main)**, local library, internet radio, SoundCloud, Bandcamp, Jamendo,
  podcasts, direct links, Spotify links via cross-source matching.
- Autoplay (YouTube mix / history / library / playlist / radio), quiet hours, blocklist,
  max volume + cooldown, panic mute.
- Download cache + prefetch + gapless preload, EBU R128 normalisation, SponsorBlock, synced lyrics.
- Recovery: track failures skipped with reasons, sound-card faults retried without losing the queue,
  mpv supervised, resume after restart.
- Reverse-proxy ready (sub-path, WebSockets, X-Forwarded-*, SSO header), API tokens + OpenAPI, PWA,
  TV mode, en/es, dark/light.

## Phase 2: premium sources and comfort (next)

1. **Spotify Connect speaker (librespot)** with an exclusive "Spotify mode" (Premium). Bundle the
   librespot binary (multi-arch release builds), pipe it to the same ALSA device, and show its
   now-playing state in the UI.
2. **ListenBrainz / Last.fm scrobbling** to an office account (ListenBrainz needs only a token).
3. **Crossfade** option (mpv `lavfi` acrossfade between preloaded files).
4. Per-person "skip quota" and "no repeats within N hours" rules.
5. QR code on the TV screen for joining.
6. Webhooks (track started, queue empty) for chat bots.

## Phase 3: more sources

1. **Tidal** via `tidalapi` (device-code login, paid plan) with clear warnings.
2. **Subsonic/Navidrome/Jellyfin** provider for offices that already have a media server.
3. **archive.org / Free Music Archive / ccMixter** Creative Commons collections.
4. **MusicBrainz + Cover Art Archive** metadata enrichment for local files and matches.
5. Optional **Invidious/Piped** fallback for YouTube search when yt-dlp is rate-limited.

## Phase 4: scale and polish

- Drag-and-drop reordering, swipe actions on mobile.
- More languages (pt, fr, de); RTL check.
- Prometheus metrics endpoint.
- Optional multi-zone (several sound cards → several queues).
