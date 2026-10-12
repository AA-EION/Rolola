# Third-party software, services and credits

Rolola's own code is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE).
Everything listed here keeps **its own license**: Rolola's license does not apply to these components
and does not change their terms. Versions are the ones in the published image (`ghcr.io/aa-eion/rolola`).

## Python libraries (installed from PyPI into the image)

| Package | Version | License | Used for |
|---|---|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | 0.143.0 | MIT | web/API framework |
| [Starlette](https://github.com/Kludex/starlette) | 1.7.0 | BSD-3-Clause | ASGI toolkit under FastAPI |
| [Pydantic](https://github.com/pydantic/pydantic) / pydantic-core | 2.14.1 / 2.50.1 | MIT | request validation, data models |
| [Uvicorn](https://github.com/Kludex/uvicorn) | 0.54.0 | BSD-3-Clause | HTTP/WebSocket server |
| [uvloop](https://github.com/MagicStack/uvloop) | 0.23.0 | MIT / Apache-2.0 | fast event loop (uvicorn extra) |
| [httptools](https://github.com/MagicStack/httptools) | 0.9.0 | MIT | HTTP parser (uvicorn extra) |
| [websockets](https://github.com/python-websockets/websockets) | 17.2 | BSD-3-Clause | WebSocket protocol |
| [watchfiles](https://github.com/samuelcolvin/watchfiles) | 1.3.0 | MIT | uvicorn extra (not used at runtime) |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 1.2.4 | BSD-3-Clause | uvicorn extra |
| [PyYAML](https://pyyaml.org/) | 6.0.3 | MIT | uvicorn extra |
| [HTTPX](https://github.com/encode/httpx) / [httpcore](https://github.com/encode/httpcore) | 0.28.1 / 1.0.9 | BSD-3-Clause | outgoing HTTP (radio, lyrics, APIs) |
| [h11](https://github.com/python-hyper/h11) | 0.16.0 | MIT | HTTP/1.1 |
| [AnyIO](https://github.com/agronholm/anyio) | 4.15.1 | MIT | async compatibility |
| [idna](https://github.com/kjd/idna) | 3.20 | BSD-3-Clause | domain names |
| [certifi](https://github.com/certifi/python-certifi) | 2026.7.22 | MPL-2.0 | CA certificates |
| [cryptography](https://github.com/pyca/cryptography) | 50.0.2 | Apache-2.0 OR BSD-3-Clause | encrypting stored credentials |
| [cffi](https://github.com/python-cffi/cffi) / [pycparser](https://github.com/eliben/pycparser) | 2.1.1 / 3.11 | MIT-0 / BSD-3-Clause | cryptography dependency |
| [**mutagen**](https://github.com/quodlibet/mutagen) | 1.48.1 | **GPL-2.0-or-later** | reading tags and cover art of local music files |
| [**yt-dlp**](https://github.com/yt-dlp/yt-dlp) | 2026.8.19 (+ runtime updates) | Unlicense | YouTube / SoundCloud / Bandcamp / link extraction (run as a separate process) |
| [yt-dlp-ejs](https://github.com/yt-dlp/ejs) | 0.8.0 | Unlicense AND MIT AND ISC | YouTube JavaScript challenge solver for yt-dlp |
| [requests](https://github.com/psf/requests), [urllib3](https://github.com/urllib3/urllib3), [charset-normalizer](https://github.com/jawah/charset_normalizer) | 2.34.2, 2.8.0, 3.5.2 | Apache-2.0, MIT, MIT | yt-dlp dependencies |
| [Brotli](https://github.com/google/brotli) | 1.2.0 | MIT | yt-dlp dependency |
| [pycryptodomex](https://github.com/Legrandin/pycryptodome) | 3.24.1 | BSD-2-Clause / Public Domain | yt-dlp dependency |
| [annotated-types](https://github.com/annotated-types/annotated-types), [annotated-doc](https://github.com/fastapi/annotated-doc), [typing-inspection](https://github.com/pydantic/typing-inspection) | 0.8.0, 0.0.5, 0.4.4 | MIT | Pydantic/FastAPI dependencies |
| [typing_extensions](https://github.com/python/typing_extensions) | 4.16.0 | PSF-2.0 | typing backports |
| [click](https://github.com/pallets/click) | 8.5.0 | BSD-3-Clause | uvicorn CLI |
| [opentelemetry-api](https://github.com/open-telemetry/opentelemetry-python) | 1.45.1 | Apache-2.0 | transitive dependency (not used) |
| [pip](https://github.com/pypa/pip) | 25.1.1 | MIT | installs yt-dlp updates at runtime |

**About mutagen (GPL-2.0-or-later).** mutagen is used unmodified, as a separate library installed from
PyPI. Its source is at <https://github.com/quodlibet/mutagen> and its license text ships inside the image
(`/opt/venv/lib/python3*/site-packages/mutagen-*.dist-info/`). See the
[License section of the README](README.md#license) for how this relates to Rolola's noncommercial license.

## Programs in the Docker image

| Program | Version | License | Used for |
|---|---|---|---|
| [mpv](https://mpv.io/) | 0.40.0 (Debian) | GPL-2.0-or-later (Debian build) | audio engine, run as a separate process |
| [FFmpeg](https://ffmpeg.org/) | 7.1.5 (Debian) | GPL-2.0-or-later (Debian build; LGPL parts) | decoding, loudness analysis (`ebur128`), run as a separate process |
| [ALSA utilities](https://www.alsa-project.org/) | 1.2.14 | GPL-2.0-or-later | diagnostics (`aplay`, `amixer`) |
| [Python](https://www.python.org/) | 3.13.5 (Debian) | PSF-2.0 | runtime |
| [Deno](https://deno.com/) | 2.9.6 | MIT | JavaScript runtime used by yt-dlp |
| [tini](https://github.com/krallin/tini) | 0.19.0 | MIT | init process |
| [curl](https://curl.se/) | 8.14.1 | curl (MIT-style) | health check |
| ca-certificates, tzdata | 20250419, 2026c | MPL-2.0 / public domain | TLS roots, time zones |
| [Debian GNU/Linux](https://www.debian.org/) 13 "trixie" (slim base image) | 20260918 | various (see `/usr/share/doc/*/copyright`) | base system |

These are unmodified Debian packages and run as separate programs. Their license and copyright files
are kept in the image under `/usr/share/doc/<package>/copyright`. The corresponding source code for
the exact versions is available from Debian at <https://sources.debian.org/> and
<https://snapshot.debian.org/>. On request we will also provide it directly
(open an issue at <https://github.com/AA-EION/Rolola/issues>).

## Web interface

The web app (HTML/CSS/JavaScript in `rolola/web/`) uses **no third-party JavaScript or CSS libraries**
and loads no external fonts or scripts. Several interface icons are adapted from
[Feather Icons](https://github.com/feathericons/feather):

> The MIT License (MIT) — Copyright (c) 2013-2023 Cole Bemis.
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
> associated documentation files (the "Software"), to deal in the Software without restriction,
> including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense,
> and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so,
> subject to the following conditions: The above copyright notice and this permission notice shall be
> included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS",
> WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.

## Online services Rolola talks to

Rolola contains no code from these services; it calls them at runtime when the corresponding source is
enabled. Their content and data belong to their owners and are used under each service's own terms
(which, for some of them, Rolola's use does not comply with; see the README's Legal section).

| Service | Used for | Notes |
|---|---|---|
| YouTube / YouTube Music (via yt-dlp) | search, audio, "similar songs" mixes | Downloading violates YouTube's Terms of Service. |
| SoundCloud, Bandcamp (via yt-dlp / public website search) | search, audio | Unofficial access. |
| Spotify public embed pages | reading song names from pasted links | Undocumented; no Spotify audio is used. |
| [radio-browser.info](https://www.radio-browser.info/) | internet radio directory | Free community service. |
| Apple iTunes Search API | podcast directory | Episodes come from each podcast's public RSS feed. |
| [Jamendo API](https://developer.jamendo.com/) | Creative Commons music (needs a free key) | Official API, Jamendo's terms apply. |
| [LRCLIB](https://lrclib.net/) | synced lyrics | Free community lyrics database. |
| [SponsorBlock](https://sponsor.ajay.app/) | skipping non-music parts of YouTube videos | SponsorBlock data is licensed CC BY-NC-SA 4.0 (attribution: sponsor.ajay.app). |

## Ideas and inspiration (no code copied)

- [Spotube](https://github.com/team-spotube/spotube) (BSD-4-Clause, by Kingkor Roy Tirtho): the split
  between metadata and audio sources, cross-source track matching, LRCLIB lyrics and SponsorBlock
  segments. Rolola reimplements these ideas in Python; no Spotube code is included.
  See [docs/SPOTUBE.md](docs/SPOTUBE.md).
- License text: [PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0)
  by the PolyForm Project.

## Development-only tools (not shipped in the image)

[pytest](https://github.com/pytest-dev/pytest) (MIT), [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio)
(Apache-2.0), [Playwright](https://github.com/microsoft/playwright) (Apache-2.0, for screenshots),
GitHub Actions and the `docker/*` actions (MIT/Apache-2.0) for CI builds.
