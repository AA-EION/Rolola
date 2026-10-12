# What we took from Spotube

[Spotube](https://github.com/team-spotube/spotube) (BSD-4-Clause, Flutter/Dart) is a desktop/mobile
client. Reviewed at commit `69a310c` (June 2026). No code was copied; the ideas below were
reimplemented in Python.

## How Spotube works today

- **Metadata plugins** handle search, albums, playlists, login and scrobbling. **Audio-source plugins**
  find a playable stream for a track (`matches(track)` → candidates, `streams(match)` → URLs). They are
  separate things.
- Plugins are compiled **Hetu-script** bytecode (`plugin.smplug` = zip with `plugin.json` + `plugin.out`)
  that runs inside the Dart app. The bundled defaults are *MusicBrainz + ListenBrainz* (metadata) and
  *YouTube Audio* (audio). Spotify is no longer bundled; the changelog shows repeated breakage
  (TOTP login change, 429s). Piped/Invidious backends also broke often.
- Neither the plugins nor Hetu can run in our Python container, so the plugin format itself isn't reusable.

## Adopted (in the MVP)

| Spotube feature | Here | File |
|---|---|---|
| Metadata/audio split + `SourcedTrack` matching with ranking and cached match + "siblings" | `Matcher`: searches audio sources (local library first), ranks by Spotube's rules **plus duration closeness and penalties for live/cover/karaoke variants**, caches the choice, keeps alternatives for "wrong match?" | `rolola/matcher.py` |
| Spotify as metadata, audio from elsewhere | **Spotify links**: paste a track/album/playlist link; names come from Spotify's public embed page; each song is matched to another source and marked as substituted | `rolola/providers/spotify_links.py` |
| Synced lyrics from LRCLIB | Lyrics for the current track (synced when available), cached, shown in party/TV mode | `rolola/lyrics.py` |
| SponsorBlock "skip non-music segments" | Skips talking intros/skits/outros in YouTube music videos (categories configurable) | `rolola/lyrics.py` (`SponsorBlock`) |
| Blacklist of tracks/artists | Already in scope (admin blocklist) | — |

## Roadmap (phase 2/3)

- **ListenBrainz / Last.fm scrobbling** to an office account. ListenBrainz only needs a user token.
- **MusicBrainz + Cover Art Archive** as a metadata source (official API, 1 req/s) for clean tags and covers.
- **Plugin discovery** (Spotube searches GitHub for the `spotube-plugin` topic). Our providers are
  in-tree Python classes, which is simpler and safer for a server. We could revisit if third parties
  want to ship providers.

## Not adopted

- Piped / Invidious / NewPipe / youtube_explode engines: yt-dlp is the most actively maintained extractor,
  and we can update it inside the container.
- Spotify login through web-player tokens (TOTP scraping): fragile and clearly against Spotify's terms.
  Real Spotify playback stays on the roadmap via librespot (Spotify Connect, Premium).
