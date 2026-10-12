# Test plan

## Automated (`pytest`, 44 tests, ~10 s)

| File | Covers |
|---|---|
| `tests/test_queue_votes.py` | FIFO vs fair round-robin ordering; least-recently-served first; pins; per-user limit (truncate, refuse, admin exempt, 0 = unlimited); remove/reorder permissions (own vs admin); admin pin with fairness on; global moves with fairness off; vote thresholds (percent/count, rounding, caps, never 0); vote dedup and reset. |
| `tests/test_rules_proxy.py` | Quiet-hours windows (exclusive end, weekends, crossing midnight); blocklist by artist/keyword/track and on substituted tracks; name validation; `X-Forwarded-*` only from trusted proxies; `BASE_PATH` stripping, root still served, `/rolola` → 308; identity header ignored from untrusted peers; proxy groups → admin; generated admin password (file mode 600); sessions, elevation, forged cookies; provider secrets encrypted at rest. |
| `tests/test_spotube_ideas.py` | Match ranking (official audio + right duration beats live/karaoke/wrong length; Topic/local bonus; unrelated songs below threshold; requested variants not penalised); title normalisation; LRC parsing; Spotify link parsing. |
| `tests/test_api.py` | End to end with **real mpv** (null output) and a generated library: health, static, OpenAPI; name required; CSRF header required; fair order across users via the API; **vote-skip with 3 live WebSocket listeners** (1 vote → not yet, 2 → skip); owner instant skip; history outcomes (`voted`, `skipped`); can't remove others' tracks; volume clamp to max + cooldown; admin login (wrong/right), settings change, diagnostics; secrets returned masked; blocklist prevents adding; duplicates refused. |

Run: `pip install -r requirements-dev.txt && pytest` (the e2e module is skipped if mpv/ffmpeg are missing).
CI runs the suite and builds the multi-arch image (`.github/workflows/ci.yml`).

## Manual checklist

Verified in development: ✅ = done in the container/proxy test run, ☐ = needs the real hardware.

### Audio (on the TrueNAS box)
- ☐ `aplay -l` on the host shows the card; `AUDIO_DEVICE=plughw:CARD=<name>,DEV=0` set.
- ☐ *Admin → Audio & diagnostics*: `/dev/snd ok (writable)`, the card is listed, `output: alsa`.
- ☐ *Play test tone*: 440 Hz is audible for 3 s, then the interrupted track resumes where it was.
- ☐ Play a local FLAC, a YouTube track and a radio station back to back: similar perceived loudness.
- ☐ Two consecutive album tracks (live album): no gap at the transition.
- ☐ Set `max_volume` to 50: the slider caps at 50 and the speakers are quieter.
- ☐ Unplug a USB sound card (or set a wrong device): red banner, the queue doesn't advance; fix it → resumes within ~10 s.
- ☐ `docker restart rolola` mid-song: comes back on the same song near the same position. (✅ verified with null output)
- ☐ Under load (library rescan + 3 YouTube downloads + 40 browser tabs): no audible glitches.

### Sources
- ✅ YouTube search returns results ranked music-first; ☐ download plays from an office IP.
- ☐ YouTube blocked → *Update yt-dlp now* → status turns "Connected" again; ☐ cookies.txt fallback works.
- ✅ Local library: scan finds tracks, album covers show, opening an album lists its tracks; ☐ "Add all".
- ☐ Internet radio: search "jazz", play, ICY title appears under the station name; custom station URL plays.
- ✅ SoundCloud search; ☐ playback.
- ☐ Bandcamp: search an album, open it, play a track.
- ☐ Podcasts: search, open the show, play an episode.
- ✅ Spotify link: track/album metadata read; ☐ plays the matched YouTube version, "via YouTube" note shows, *Wrong song?* swaps it.
- ☐ Jamendo: add a client ID → *Test* → OK → search and play.
- ☐ Unplug the internet: local library still searches and plays; remote sources show "Offline"; the UI explains the errors.

### Reverse proxy
- ✅ nginx sub-path `/rolola/` with prefix stripping: UI loads, WebSocket connects, cookie path `/rolola/`.
- ✅ `BASE_PATH=/rolola` without stripping (Traefik style): UI, API and WebSocket work; `/rolola` → `/rolola/`.
- ✅ SSO header from the trusted proxy → logged in without a name prompt; group → admin.
- ✅ The same header sent by a client (proxy clears it) → ignored.
- ✅ Cross-origin WebSocket → rejected.
- ☐ Behind TLS: `X-Forwarded-Proto: https` → cookies marked `Secure`; PWA installable on Android/iOS.

### UX / accessibility
- ✅ Phone (390 px): every main action reachable one-handed; search → tap → added (toast with queue position).
- ✅ Desktop three-column layout; TV mode (`#/display`) with progress, next up, synced lyrics.
- ✅ Spanish and English; light and dark themes.
- ☐ Keyboard only: Tab through nav, search, results (Enter adds), sheets close with Esc.
- ☐ Screen reader (TalkBack/VoiceOver): buttons announce "Add X to the queue", "Vote to skip", progress.
