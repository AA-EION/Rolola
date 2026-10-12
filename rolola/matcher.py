"""Cross-source matching ("play this Spotify track from YouTube").

Adapted from Spotube's split between *metadata* sources (know what a song
is) and *audio* sources (can actually play it): when a track comes from a
source that cannot play audio here (Spotify links, later MusicBrainz or
Tidal metadata), we search the enabled audio sources, rank the candidates,
remember the choice, and keep the runners-up so anyone can fix a wrong
match.  The UI always shows that a substitution happened.

The scoring follows Spotube's ``SourcedTrack.rankResults`` (artist in
channel/title, track name in title, "official audio" flags) and adds a
duration check, which removes most live versions, covers and hour-long
loops.
"""

from __future__ import annotations

import re
import time
import unicodedata
from typing import TYPE_CHECKING

from .db import dumps, loads
from .logs import get_logger
from .models import Track

if TYPE_CHECKING:
    from .services import Services

log = get_logger("matcher")

OFFICIAL_RE = re.compile(r"official\s(video|audio|music\svideo|lyric\svideo|visualizer)", re.I)
BAD_RE = re.compile(r"\b(live|cover|karaoke|instrumental|remix|sped up|slowed|reverb|8d|nightcore|reaction|tutorial|1 hour|10 hours)\b", re.I)
# audio sources tried in order; local first so the NAS wins when it has the song
AUDIO_ORDER = ("local", "youtube", "soundcloud", "jamendo", "bandcamp")


def norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)  # (feat. x), [Remastered]
    text = re.sub(r"\b(feat|ft)\.?\s.*$", " ", text)
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def score(candidate: Track, wanted: Track) -> float:
    """Higher is better. Roughly: >= 4 is a confident match."""
    s = 0.0
    c_title = norm(candidate.title)
    c_artist = norm(candidate.artist)
    w_title = norm(wanted.title)
    artists = [norm(a) for a in re.split(r",|&| x | and ", wanted.artist or "") if a.strip()]

    for a in artists:
        if a and (a == c_artist or (c_artist and (a in c_artist or c_artist in a))):
            s += 1
        if a and a in c_title:
            s += 1
    if w_title and w_title in c_title:
        s += 3
    elif w_title and c_title and set(w_title.split()) <= set(c_title.split()):
        s += 2
    official = bool(OFFICIAL_RE.search(candidate.title))
    if official:
        s += 1
        if w_title and w_title in c_title:
            s += 2
    if (candidate.artist or "").endswith(" - Topic") or candidate.source == "local":
        s += 1
    # penalise variants the user didn't ask for
    wanted_flags = set(BAD_RE.findall(wanted.title or ""))
    for flag in BAD_RE.findall(candidate.title or ""):
        if flag.lower() not in {f.lower() for f in wanted_flags}:
            s -= 2
    if wanted.duration and candidate.duration:
        diff = abs(candidate.duration - wanted.duration)
        if diff <= 3:
            s += 2
        elif diff <= 10:
            s += 1
        elif diff > 60:
            s -= 3
    return s


def rank(candidates: list[Track], wanted: Track) -> list[tuple[float, Track]]:
    scored = [(score(c, wanted), c) for c in candidates if not c.is_container and not c.live]
    # stable: keeps source order (local first) on ties
    return sorted(scored, key=lambda p: -p[0])


class Matcher:
    MIN_SCORE = 3.0

    def __init__(self, services: "Services"):
        self.svc = services

    def cached(self, key: str) -> tuple[Track, list[Track]] | None:
        row = self.svc.db.one("SELECT * FROM source_match WHERE query_key=?", (key,))
        if not row:
            return None
        return (Track.model_validate(loads(row["match"])),
                [Track.model_validate(t) for t in loads(row["alternatives"], [])])

    def _store(self, key: str, match: Track, alts: list[Track], manual: bool = False) -> None:
        self.svc.db.execute(
            "INSERT OR REPLACE INTO source_match(query_key, match, alternatives, manual, created_at) VALUES (?,?,?,?,?)",
            (key, dumps(match.model_dump()), dumps([a.model_dump() for a in alts[:8]]), int(manual), time.time()),
        )

    async def find(self, wanted: Track) -> Track | None:
        """Best playable equivalent of ``wanted`` from the enabled audio sources."""
        hit = self.cached(wanted.key)
        if hit:
            return self._mark(hit[0], wanted)
        query = f"{wanted.artist or ''} {wanted.title}".strip()
        candidates: list[Track] = []
        for pid in AUDIO_ORDER:
            prov = self.svc.providers.get(pid)
            if not prov or not prov.enabled or not prov.configured():
                continue
            try:
                found = await prov.search(query, limit=8)
            except Exception as exc:
                log.info("match search failed on %s: %s", pid, exc)
                continue
            ranked = rank(found, wanted)
            candidates += [t for _, t in ranked]
            # a confident hit from an earlier (preferred) source ends the search
            if ranked and ranked[0][0] >= self.MIN_SCORE + 2:
                break
        ranked = rank(candidates, wanted)
        if not ranked or ranked[0][0] < self.MIN_SCORE:
            return None
        best = ranked[0][1]
        self._store(wanted.key, best, [t for _, t in ranked[1:]])
        return self._mark(best, wanted)

    def choose(self, wanted_key: str, alternative: Track) -> None:
        """Someone said "wrong match": use this alternative from now on."""
        hit = self.cached(wanted_key)
        alts = [a for a in (hit[1] if hit else []) if a.key != alternative.key]
        if hit:
            alts.insert(0, hit[0])
        self._store(wanted_key, alternative, alts, manual=True)

    @staticmethod
    def _mark(match: Track, wanted: Track) -> Track:
        out = match.model_copy(deep=True)
        out.substituted_from = {"source": wanted.source, "id": wanted.id, "key": wanted.key,
                                "title": wanted.title, "artist": wanted.artist, "url": wanted.url}
        # show the metadata people asked for; artwork from the original when we have it
        out.artwork = wanted.artwork or out.artwork
        return out
