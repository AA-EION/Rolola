"""The playback controller: decides what plays next and keeps mpv busy.

Flow
----
* The queue lives in SQLite. The *next* item is resolved ahead of time
  (download, match, loudness) by the prefetcher, then appended to mpv's
  playlist so mpv starts it gaplessly when the current one ends.
* mpv events (``start-file``/``end-file``) drive state transitions.
* A track that fails is skipped with a plain-language toast; a failing sound
  card does *not* consume the queue: the track is kept and retried.
* The current track and position are saved every few seconds so a restart
  or image update resumes where it stopped.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .db import dumps, loads
from .engine import EngineError, MpvEngine
from .logs import get_logger
from .models import Playable, Track
from .providers.base import ProviderError
from .queue import QueueItem, QueueManager
from .rules import next_window_start, schedule_allows
from .votes import VoteBox, required_votes

if TYPE_CHECKING:
    from .services import Services

log = get_logger("player")
TEST_TONE = "av://lavfi:sine=frequency=440:duration=3"
AUDIO_FAULT_MARKERS = ("audio output", "audio device", "ao init")


@dataclass
class Pending:
    """A track chosen to play (from the queue, autoplay, or a resume)."""
    track: Track
    user_id: int | None
    autoplay: bool = False
    queue_item_id: int | None = None
    resume_at: float | None = None


@dataclass
class Current:
    pending: Pending
    track: Track                  # what actually plays (maybe substituted)
    playable: Playable
    play_id: int | None
    mpv_id: int | None
    started_at: float
    test: bool = False
    icy_title: str | None = None
    likes: set[int] = field(default_factory=set)
    segments: list[tuple[float, float]] = field(default_factory=list)
    skip_reason: str | None = None   # set when we skip it


class Player:
    def __init__(self, svc: "Services"):
        self.svc = svc
        self.settings = svc.settings
        self.queue = QueueManager(svc.db, svc.settings.get, self._on_queue_change)
        self.engine = MpvEngine(
            svc.config.run_dir / "mpv.sock", svc.config.mpv_binary, svc.config.mpv_extra_args,
            on_event=self._on_event, on_restart=self._on_engine_restart,
        )
        self.engine.disable_audio = svc.config.disable_audio
        self.current: Current | None = None
        self.loading: Pending | None = None
        self.preloaded: tuple[int, Pending, Track, Playable] | None = None  # (mpv_id, ...)
        self.paused = False
        self.paused_by_schedule = False
        self.paused_since: float | None = None   # when the current pause started
        self.stopped = False                      # stopped by an admin: stay silent until someone acts
        self.muted = False
        self.position = 0.0
        self.duration: float | None = None
        self.position_at = time.time()
        self.audio_fault: str | None = None
        self.votes = VoteBox()
        self.autoplay_buffer: list[Track] = []
        self._resume: Pending | None = None
        self._ready: dict[str, tuple[Track, Playable, float]] = {}
        self._prefetching: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._advance_task: asyncio.Task | None = None
        self._tasks: list[asyncio.Task] = []
        self._wake_prefetch = asyncio.Event()
        self._fail_streak = 0
        self._last_volume_change: dict[int, float] = {}
        self._user_names: dict[int, str] = {}
        self._autoplay_filling = False
        self._autoplay_backoff: dict[str, float] = {}
        self._last_trim = 0.0

    # ------------------------------------------------------------------ setup
    @property
    def volume(self) -> int:
        return int(self.settings.get("volume"))

    def audio_device(self) -> str:
        return self.settings.get("audio_device") or self.svc.config.audio_device

    async def start(self) -> None:
        self.engine.device = self.audio_device()
        self.engine.initial_volume = min(self.volume, int(self.settings.get("max_volume")))
        self.stopped = bool(self.settings.get("player_stopped"))
        saved = self.settings.get("player_resume")
        if saved:
            since = saved.get("paused_since") if saved.get("paused") else saved.get("saved_at")
            if self._is_stale(since):
                # paused (or switched off) long ago: start fresh instead of resuming yesterday's music
                log.info("discarding stale playback state from before the restart")
                self.settings.reset("player_resume")
                self.queue.remove_older_than(since)
                saved = None
        if saved:
            try:
                self._resume = Pending(track=Track.model_validate(saved["track"]), user_id=saved.get("user_id"),
                                       autoplay=saved.get("autoplay", False), resume_at=saved.get("position"))
                self.paused = bool(saved.get("paused"))
                self.paused_since = saved.get("paused_since") if self.paused else None
            except Exception:
                self._resume = None
        await self.engine.start()
        self._tasks += [
            asyncio.create_task(self._poll_loop(), name="player-poll"),
            asyncio.create_task(self._prefetch_loop(), name="player-prefetch"),
            asyncio.create_task(self._housekeeping_loop(), name="player-housekeeping"),
        ]
        await asyncio.wait_for(self.engine.ready.wait(), 10)
        self.kick()

    async def stop(self) -> None:
        self._save_resume()
        for t in self._tasks:
            t.cancel()
        await self.engine.stop()

    # ------------------------------------------------------------ helpers
    def _toast(self, key: str, level: str = "info", user_id: int | None = None, **params: Any) -> None:
        msg = {"type": "toast", "key": key, "level": level, "params": params}
        if user_id is not None:
            self.svc.hub.send_to_user(user_id, msg)
        else:
            self.svc.hub.broadcast(msg)

    def user_name(self, uid: int | None) -> str | None:
        if uid is None:
            return None
        if uid not in self._user_names:
            row = self.svc.db.one("SELECT name FROM users WHERE id=?", (uid,))
            self._user_names[uid] = row["name"] if row else "?"
        return self._user_names[uid]

    def forget_user_name(self, uid: int) -> None:
        self._user_names.pop(uid, None)

    def changed(self) -> None:
        self.svc.hub.state_changed()

    def _on_queue_change(self) -> None:
        self._wake_prefetch.set()
        self.changed()

    def _can_play(self) -> bool:
        if self.settings.get("schedule_enabled") and not schedule_allows(self.settings.get("schedule")):
            return False
        return not self.paused and not self.stopped

    def _is_stale(self, since: float | None) -> bool:
        minutes = int(self.settings.get("stale_minutes") or 0)
        return bool(minutes > 0 and since and time.time() - since > minutes * 60)

    def status(self) -> str:
        if self.audio_fault:
            return "audio_error"
        if self.settings.get("schedule_enabled") and not schedule_allows(self.settings.get("schedule")):
            return "quiet_hours"
        if self.loading and not self.current:
            return "loading"
        if self.current:
            return "paused" if self.paused else "playing"
        return "paused" if self.paused else "idle"

    def live_position(self) -> float:
        if self.current and not self.paused and self.status() == "playing":
            return self.position + (time.time() - self.position_at)
        return self.position

    # ------------------------------------------------------------ state for clients
    def snapshot(self) -> dict:
        cur = self.current
        now = None
        if cur:
            now = {
                "play_id": cur.play_id,
                "track": cur.track.public(),
                "user": {"id": cur.pending.user_id, "name": self.user_name(cur.pending.user_id)} if cur.pending.user_id else None,
                "autoplay": cur.pending.autoplay,
                "test": cur.test,
                "started_at": cur.started_at,
                "icy_title": cur.icy_title,
                "likes": len(cur.likes),
                "liked_by": sorted(cur.likes),
                "segments": cur.segments,
            }
        listeners = self.svc.hub.listeners()
        mode, value = self.settings.get("skip_mode"), self.settings.get("skip_value")
        queue = []
        for it in self.queue.ordered()[:300]:
            queue.append({
                "id": it.id, "track": it.track.public(), "pinned": it.pinned, "added_at": it.added_at,
                "user": {"id": it.user_id, "name": self.user_name(it.user_id)},
                "ready": it.track.key in self._ready,
            })
        loading = None
        if self.loading:
            loading = {"track": self.loading.track.public(), "autoplay": self.loading.autoplay,
                       "user": {"id": self.loading.user_id, "name": self.user_name(self.loading.user_id)} if self.loading.user_id else None}
        quiet_until = None
        if self.status() == "quiet_hours":
            nxt = next_window_start(self.settings.get("schedule"))
            quiet_until = nxt.strftime("%a %H:%M") if nxt else None
        return {
            "status": self.status(),
            "now": now,
            "loading": loading,
            "position": round(self.live_position(), 2),
            "duration": self.duration if (cur and not cur.track.live) else None,
            "server_time": time.time(),
            "paused": self.paused,
            "muted": self.muted,
            "volume": self.volume,
            "audio_fault": self.audio_fault,
            "quiet_until": quiet_until,
            "queue": queue,
            "queue_total": len(self.queue),
            "listeners": listeners,
            "votes": {"count": len(self.votes.voters), "voters": sorted(self.votes.voters),
                      "required": required_votes(len(listeners), mode, value)},
            "autoplay": {"mode": self.settings.get("autoplay_mode"),
                         "next": self.autoplay_buffer[0].public() if self.autoplay_buffer else None},
            "settings": self.settings.all_public(),
        }

    # ------------------------------------------------------------ resolution
    def _provider_for(self, track: Track):
        prov = self.svc.provider(track.source)
        if prov is None:
            raise ProviderError("This source no longer exists.")
        return prov

    async def prepare(self, track: Track, wait_loudness: float = 30.0) -> tuple[Track, Playable]:
        """Turn a queued track into something mpv can open (match, download, gain)."""
        if (reason := self.svc.blocklist.reason(track)):
            raise ProviderError(f"Blocked by the admin ({reason}).")
        prov = self._provider_for(track)
        play_track = track
        needs_match = "metadata_only" in prov.capabilities or (
            not prov.enabled and self.settings.get("substitute_unavailable"))
        if needs_match:
            match = await self.svc.matcher.find(track)
            if match is None:
                raise ProviderError("No playable version found on the enabled sources.")
            if (reason := self.svc.blocklist.reason(match)):
                raise ProviderError(f"Blocked by the admin ({reason}).")
            play_track = match
            prov = self._provider_for(match)
        elif not prov.enabled:
            raise ProviderError(f"{prov.name} is disabled.")
        playable = await asyncio.wait_for(prov.resolve(play_track), timeout=240)
        playable.live = playable.live or play_track.live
        if self.settings.get("normalize"):
            if playable.live:
                playable.gain_db = float(self.settings.get("stream_gain_db"))
            elif playable.cache_id:
                if not self.svc.cache.has_loudness(playable.cache_id):
                    task = self.svc.cache.measure_soon(playable.cache_id, Path(playable.target))
                    if task:
                        try:
                            await asyncio.wait_for(asyncio.shield(task), wait_loudness)
                        except asyncio.TimeoutError:
                            pass
                lufs = self.svc.cache.loudness(playable.cache_id)
                if lufs is not None:
                    gain = float(self.settings.get("target_lufs")) - lufs
                    playable.gain_db = max(-15.0, min(10.0, gain))
        return play_track, playable

    def _load_options(self, playable: Playable, resume_at: float | None) -> dict[str, str]:
        opts: dict[str, str] = {}
        if not self.settings.get("normalize"):
            opts["replaygain"] = "no"
        elif playable.gain_db is not None:
            opts["replaygain-fallback"] = f"{playable.gain_db:.2f}"
        if resume_at and resume_at > 3 and not playable.live:
            opts["start"] = f"{resume_at - 2:.1f}"
        return opts

    # ------------------------------------------------------------ choosing what's next
    def _peek_next(self) -> Pending | None:
        if self._resume:
            return self._resume
        head = self.queue.head()
        if head:
            return Pending(head.track, head.user_id, queue_item_id=head.id)
        if self.autoplay_buffer and self.settings.get("autoplay_mode") != "off":
            return Pending(self.autoplay_buffer[0], None, autoplay=True)
        return None

    def _claim(self, p: Pending) -> None:
        """Remove ``p`` from wherever it came from."""
        if p is self._resume:
            self._resume = None
        elif p.queue_item_id is not None:
            self.queue.take(p.queue_item_id)
        elif p.autoplay and self.autoplay_buffer and self.autoplay_buffer[0].key == p.track.key:
            self.autoplay_buffer.pop(0)

    def kick(self) -> None:
        """Start playing if we should be and aren't."""
        if self.current or (self._advance_task and not self._advance_task.done()):
            return
        if not self._can_play():
            return
        if self._peek_next() is None:
            self._ensure_autoplay()
            return
        self._advance_task = asyncio.create_task(self._advance())

    async def _advance(self) -> None:
        """Pick the next track, prepare it (may take a while) and load it."""
        while True:
            async with self._lock:
                if self.current or not self._can_play():
                    self.loading = None
                    return
                pending = self._peek_next()
                if pending is None:
                    self.loading = None
                    self.changed()
                    self._ensure_autoplay()
                    if self.engine.alive:
                        try:
                            await self.engine.command("stop")
                        except EngineError:
                            pass
                    return
                self._claim(pending)
                self.loading = pending
                self.changed()
            try:
                ready = self._ready.pop(pending.track.key, None)
                if ready:
                    play_track, playable, _ = ready
                else:
                    play_track, playable = await self.prepare(pending.track, wait_loudness=6.0)
            except asyncio.CancelledError:
                self.loading = None
                raise
            except Exception as exc:
                msg = str(exc) or "Timed out."
                if not isinstance(exc, (ProviderError, asyncio.TimeoutError)):
                    log.exception("unexpected error preparing %s", pending.track.key)
                    msg = "Unexpected error."
                self._record_failure(pending, msg)
                self.loading = None
                self._fail_streak += 1
                if self._fail_streak >= 5:
                    self._toast("toast.too_many_failures", "error")
                    self._fail_streak = 0
                    await asyncio.sleep(20)
                continue
            async with self._lock:
                self.loading = None
                if self.current or not self._can_play():
                    # paused while loading: put it back in front
                    self._resume = pending
                    self._ready[pending.track.key] = (play_track, playable, time.time())
                    self.changed()
                    return
                await self._load_now(pending, play_track, playable)
                return

    async def _load_now(self, pending: Pending, play_track: Track, playable: Playable) -> None:
        try:
            mpv_id = await self.engine.loadfile(playable.target, "replace", self._load_options(playable, pending.resume_at))
            await self.engine.set("pause", False)
        except EngineError as exc:
            log.error("mpv refused to load: %s", exc)
            self._resume = pending
            self.audio_fault = self.audio_fault or "The audio engine is restarting."
            self.changed()
            return
        self.preloaded = None
        self._begin(pending, play_track, playable, mpv_id)

    def _begin(self, pending: Pending, play_track: Track, playable: Playable, mpv_id: int | None) -> None:
        now = time.time()
        play_id = self.svc.db.execute(
            "INSERT INTO history(track_key, track, user_id, autoplay, started_at) VALUES (?,?,?,?,?)",
            (play_track.key, dumps(play_track.model_dump()), pending.user_id, int(pending.autoplay), now),
        ).lastrowid
        if not pending.autoplay:
            self.queue.mark_served(pending.user_id, now)
        self.current = Current(pending, play_track, playable, play_id, mpv_id, now)
        self.position = pending.resume_at or 0.0
        self.position_at = now
        self.duration = play_track.duration
        self.votes.reset(play_id)
        self._fail_streak = 0
        self.changed()
        self._wake_prefetch.set()
        if self.settings.get("sponsorblock"):
            asyncio.create_task(self._load_segments(self.current))
        log.info("now playing", extra={"ctx": {"track": play_track.key, "user": pending.user_id,
                                               "autoplay": pending.autoplay, "gain": playable.gain_db}})

    async def _load_segments(self, cur: Current) -> None:
        vid = cur.track.id if cur.track.source == "youtube" else None
        if not vid:
            return
        segs = await self.svc.sponsorblock.segments(vid, list(self.settings.get("sponsorblock_categories") or []))
        if self.current is cur:
            cur.segments = segs
            self.changed()

    def _record_failure(self, pending: Pending, msg: str) -> None:
        self.svc.db.execute(
            "INSERT INTO history(track_key, track, user_id, autoplay, started_at, ended_at, outcome, error) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (pending.track.key, dumps(pending.track.model_dump()), pending.user_id, int(pending.autoplay),
             time.time(), time.time(), "error", msg[:300]),
        )
        log.warning("track failed", extra={"ctx": {"track": pending.track.key, "error": msg}})
        if pending.autoplay:
            # nobody picked it: don't alarm the room, and stop leaning on a failing source
            self._autoplay_backoff[pending.track.source] = time.time() + 900
            if self.autoplay_buffer and self.autoplay_buffer[0].key == pending.track.key:
                self.autoplay_buffer.pop(0)
            self._fail_streak = max(0, self._fail_streak - 1)
        else:
            self._toast("toast.track_failed", "error", title=pending.track.title, reason=msg)

    def _finish(self, outcome: str) -> None:
        cur = self.current
        if not cur:
            return
        if cur.play_id:
            self.svc.db.execute("UPDATE history SET ended_at=?, outcome=?, likes=? WHERE id=?",
                                (time.time(), cur.skip_reason or outcome, len(cur.likes), cur.play_id))
        self.current = None
        self.votes.reset(None)
        self.position = 0.0
        self.duration = None
        self.changed()

    # ------------------------------------------------------------ gapless preload
    async def _refresh_preload(self) -> None:
        """Keep exactly the right *next* file appended in mpv for gapless playback."""
        cur = self.current
        want: Pending | None = None
        if cur and not cur.playable.live and not cur.test and self._can_play():
            p = self._peek_next()
            if p and not p.track.live and p.track.key in self._ready and p is not self._resume:
                want = p
        have = self.preloaded
        same = have and want and have[1].track.key == want.track.key and have[1].queue_item_id == want.queue_item_id
        if same:
            return
        try:
            if have:
                await self.engine.command("playlist-clear")
                self.preloaded = None
            if want:
                play_track, playable, _ = self._ready[want.track.key]
                mpv_id = await self.engine.loadfile(playable.target, "append", self._load_options(playable, None))
                self.preloaded = (mpv_id, want, play_track, playable)
        except EngineError:
            self.preloaded = None

    # ------------------------------------------------------------ mpv events
    async def _on_event(self, ev: dict) -> None:
        kind = ev.get("event")
        if kind == "property-change":
            await self._on_property(ev.get("name"), ev.get("data"))
            return
        if kind not in ("start-file", "end-file", "playback-restart"):
            return
        async with self._lock:
            if kind == "start-file":
                await self._on_start_file(ev.get("playlist_entry_id"))
            elif kind == "end-file":
                await self._on_end_file(ev)
            elif kind == "playback-restart":
                self.position = await self.engine.get("time-pos", self.position) or self.position
                self.position_at = time.time()
                if self.audio_fault and self.current:
                    self.audio_fault = None
                    self._toast("toast.audio_back", "success")
                self.changed()

    async def _on_start_file(self, mpv_id: int | None) -> None:
        pre = self.preloaded
        if pre and mpv_id == pre[0]:
            _, pending, play_track, playable = pre
            self.preloaded = None
            self._finish("played")
            self._claim(pending)
            self._ready.pop(pending.track.key, None)
            self._begin(pending, play_track, playable, mpv_id)

    async def _on_end_file(self, ev: dict) -> None:
        cur = self.current
        mpv_id, reason = ev.get("playlist_entry_id"), ev.get("reason")
        if not cur or mpv_id != cur.mpv_id:
            return
        if reason == "eof":
            if self.preloaded:
                return  # start-file for the preloaded entry follows
            self._finish("played")
            self.kick()
        elif reason == "error":
            err = ev.get("file_error") or "unknown error"
            if any(m in err.lower() for m in AUDIO_FAULT_MARKERS):
                self._audio_failed(cur, err)
                return
            self._finish("error")
            if not cur.test:
                self._toast("toast.track_failed", "error", title=cur.track.title, reason=f"Playback error: {err}")
            if self.preloaded:
                return
            self.kick()

    def _audio_failed(self, cur: Current, err: str) -> None:
        first = self.audio_fault is None
        self.audio_fault = (f"Sound card unavailable ({err}). Device: {self.audio_device()}. "
                            "Retrying every 10 seconds.")
        if cur.play_id:
            self.svc.db.execute("DELETE FROM history WHERE id=?", (cur.play_id,))
        if not cur.test:
            self._resume = Pending(cur.pending.track, cur.pending.user_id, cur.pending.autoplay,
                                   resume_at=self.live_position())
            self._ready[cur.pending.track.key] = (cur.track, cur.playable, time.time())
        self.current = None
        self.preloaded = None
        if first:
            log.error("audio output failed: %s", err)
            self._toast("toast.audio_error", "error", device=self.audio_device())
        self.changed()

    async def _on_property(self, name: str | None, data: Any) -> None:
        if name == "metadata" and self.current and isinstance(data, dict):
            title = data.get("icy-title") or data.get("ICY-TITLE")
            if title != self.current.icy_title:
                self.current.icy_title = title
                self.changed()
        elif name == "duration" and self.current and isinstance(data, (int, float)):
            self.duration = float(data)
            self.changed()
        elif name == "seeking" and data is False:
            self.position = await self.engine.get("time-pos", self.position) or self.position
            self.position_at = time.time()
            self.changed()

    async def _on_engine_restart(self) -> None:
        async with self._lock:
            cur = self.current
            if cur and not cur.test:
                self._resume = Pending(cur.pending.track, cur.pending.user_id, cur.pending.autoplay,
                                       resume_at=self.live_position())
                if cur.play_id:
                    self.svc.db.execute("DELETE FROM history WHERE id=?", (cur.play_id,))
            self.current = None
            self.preloaded = None
            await self.engine.set("volume", min(self.volume, int(self.settings.get("max_volume"))))
            self.changed()
        self.kick()

    # ------------------------------------------------------------ background loops
    async def _poll_loop(self) -> None:
        last_save = 0.0
        while True:
            await asyncio.sleep(0.5)
            try:
                cur = self.current
                if cur and self.engine.alive and not self.paused:
                    pos = await self.engine.get("time-pos")
                    if isinstance(pos, (int, float)):
                        self.position, self.position_at = float(pos), time.time()
                        await self._apply_segments(cur, float(pos))
                if time.time() - last_save > 5:
                    last_save = time.time()
                    self._save_resume()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("poll loop error")

    async def _apply_segments(self, cur: Current, pos: float) -> None:
        for start, end in cur.segments:
            if start <= pos < end - 1:
                if self.duration and end >= self.duration - 1:
                    log.info("sponsorblock: skipping outro")
                    await self.skip(None, reason="sponsorblock", force=True)
                else:
                    log.info("sponsorblock: skipping %.0f-%.0f", start, end)
                    try:
                        await self.engine.set("time-pos", end)
                    except EngineError:
                        pass
                return

    def _save_resume(self) -> None:
        cur = self.current
        p = cur.pending if cur and not cur.test else (self._resume or self.loading)
        if p:
            self.settings.set("player_resume", {
                "track": p.track.model_dump(), "user_id": p.user_id, "autoplay": p.autoplay,
                "position": self.live_position() if cur else p.resume_at, "paused": self.paused,
                "paused_since": self.paused_since, "saved_at": time.time(),
            })
        elif self.settings.get("player_resume"):
            self.settings.reset("player_resume")

    async def _prefetch_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake_prefetch.wait(), 15)
            except asyncio.TimeoutError:
                pass
            self._wake_prefetch.clear()
            try:
                await self._prefetch_once()
                async with self._lock:
                    await self._refresh_preload()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("prefetch error")

    async def _prefetch_once(self) -> None:
        n = max(1, int(self.settings.get("prefetch_count") or 2))
        wanted: list[Track] = []
        if self._resume:
            wanted.append(self._resume.track)
        wanted += [i.track for i in self.queue.ordered()[:n]]
        if len(wanted) < n and self.settings.get("autoplay_mode") != "off":
            self._ensure_autoplay()
            wanted += self.autoplay_buffer[: n - len(wanted)]
        keys = {t.key for t in wanted}
        # forget prepared items that left the queue (cached files stay on disk)
        for k in list(self._ready):
            if k not in keys:
                self._ready.pop(k, None)
        for t in wanted:
            if t.live or t.key in self._ready or t.key in self._prefetching:
                continue
            self._prefetching[t.key] = asyncio.create_task(self._prefetch_one(t))

    async def _prefetch_one(self, track: Track) -> None:
        try:
            play_track, playable = await self.prepare(track)
            self._ready[track.key] = (play_track, playable, time.time())
            self.changed()
            self._wake_prefetch.set()
        except Exception as exc:  # it will be retried (and reported) when it's due to play
            log.info("prefetch failed for %s: %s", track.key, exc)
        finally:
            self._prefetching.pop(track.key, None)

    async def _housekeeping_loop(self) -> None:
        while True:
            await asyncio.sleep(10)
            try:
                allowed = not self.settings.get("schedule_enabled") or schedule_allows(self.settings.get("schedule"))
                if not allowed and self.current and not self.paused:
                    log.info("quiet hours started: pausing")
                    await self.engine.set("pause", True)
                    self.paused = self.paused_by_schedule = True
                    self.paused_since = time.time()
                    self.changed()
                elif allowed and self.paused_by_schedule:
                    log.info("quiet hours ended: resuming")
                    self.paused_by_schedule = False
                    await self.resume_playback()
                if self.paused and self._is_stale(self.paused_since):
                    # nobody came back: forget the paused song and the old queue. After quiet
                    # hours autoplay may start again; after a manual pause stay silent.
                    log.info("paused for too long: clearing the paused song and the queue")
                    await self.stop_all(clear_queue=True, keep_silent=not self.paused_by_schedule)
                if self.audio_fault and self.engine.alive and not self.current:
                    self.kick()  # retry the sound card
                elif not self.current:
                    self.kick()
                if time.time() - self._last_trim > 600:
                    self._last_trim = time.time()
                    keep = {Path(p.target).stem for _, p, _ in self._ready.values()}
                    if self.current:
                        keep.add(Path(self.current.playable.target).stem)
                    self.svc.cache.trim(keep=keep)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("housekeeping error")

    # ------------------------------------------------------------ autoplay
    def _ensure_autoplay(self) -> None:
        mode = self.settings.get("autoplay_mode")
        if mode == "off" or len(self.autoplay_buffer) >= 2 or self._autoplay_filling:
            return
        self._autoplay_filling = True
        asyncio.create_task(self._fill_autoplay(mode))

    async def _fill_autoplay(self, mode: str) -> None:
        try:
            picks = await self._autoplay_candidates(mode)
            recent = {r["track_key"] for r in self.svc.db.all(
                "SELECT track_key FROM history ORDER BY started_at DESC LIMIT 60")}
            queued = {t.key for t in self.autoplay_buffer}
            for t in picks:
                if self._autoplay_backoff.get(t.source, 0) > time.time():
                    continue
                if t.key in recent or t.key in queued or self.svc.blocklist.reason(t):
                    continue
                if t.duration and t.duration > int(self.settings.get("max_track_minutes")) * 60:
                    continue
                self.autoplay_buffer.append(t)
                queued.add(t.key)
                if len(self.autoplay_buffer) >= 5:
                    break
            if not self.autoplay_buffer and mode != "local_shuffle":
                local = self.svc.provider("local")
                if local and local.enabled:
                    self.autoplay_buffer += await local.random_tracks(3)
        except Exception as exc:
            log.warning("autoplay failed: %s", exc)
        finally:
            self._autoplay_filling = False
        if self.autoplay_buffer:
            self.changed()
            self._wake_prefetch.set()
            self.kick()

    async def _autoplay_candidates(self, mode: str) -> list[Track]:
        db = self.svc.db
        if mode == "playlist" and self.settings.get("autoplay_playlist_id"):
            rows = db.all("SELECT track FROM playlist_items WHERE playlist_id=?", (self.settings.get("autoplay_playlist_id"),))
            tracks = [Track.model_validate(loads(r["track"])) for r in rows]
            random.shuffle(tracks)
            return tracks
        if mode == "radio" and self.settings.get("autoplay_station"):
            return [Track.model_validate(self.settings.get("autoplay_station"))]
        if mode == "history":
            rows = db.all("SELECT track FROM history WHERE outcome='played' AND autoplay=0 AND started_at > ? "
                          "GROUP BY track_key ORDER BY RANDOM() LIMIT 20", (time.time() - 60 * 86400,))
            return [Track.model_validate(loads(r["track"])) for r in rows]
        if mode == "similar":
            seeds = db.all("SELECT track FROM history WHERE outcome='played' ORDER BY started_at DESC LIMIT 5")
            seeds = [Track.model_validate(loads(r["track"])) for r in seeds]
            random.shuffle(seeds)
            for seed in seeds[:2]:
                for pid in ("youtube", "local"):
                    prov = self.svc.provider(pid)
                    if self._autoplay_backoff.get(pid, 0) > time.time():
                        continue
                    if prov and prov.enabled and "similar" in prov.capabilities:
                        try:
                            found = await prov.similar(seed, 10)
                        except Exception:
                            found = []
                        if found:
                            return found
            # nothing played yet: fall back to what people liked before, then the library
            rows = db.all("SELECT track FROM history WHERE outcome='played' GROUP BY track_key ORDER BY RANDOM() LIMIT 10")
            if rows:
                return [Track.model_validate(loads(r["track"])) for r in rows]
        local = self.svc.provider("local")
        if local and local.enabled:
            return await local.random_tracks(10)
        return []

    # ------------------------------------------------------------ commands
    async def on_tracks_added(self, items: list[QueueItem]) -> None:
        if self.paused and self._is_stale(self.paused_since):
            # yesterday's paused song and leftovers go; the new track plays right away
            since = self.paused_since
            await self.stop_all(clear_queue=False, keep_silent=False)
            self.queue.remove_older_than(since)
        elif self.paused and not self.current:
            self.paused, self.paused_since = False, None  # nothing to resume: just play the new track
        if self.stopped:
            self._set_stopped(False)
        cur = self.current
        if cur and cur.pending.autoplay and (self.settings.get("autoplay_yield") or cur.track.live):
            asyncio.create_task(self.skip(None, reason="autoplay_yield", force=True))
        self.kick()

    async def skip(self, user_id: int | None, reason: str = "skipped", force: bool = False) -> None:
        async with self._lock:
            cur = self.current
            if not cur:
                if self.loading and self._advance_task and force:
                    self._advance_task.cancel()
                    self.loading = None
                    self.changed()
                    self.kick()
                return
            cur.skip_reason = {"voted": "voted", "sponsorblock": "played", "autoplay_yield": "played"}.get(reason, "skipped")
            if self.preloaded:
                try:
                    await self.engine.command("playlist-next", "force")
                    return  # start-file of the preloaded entry handles the transition
                except EngineError:
                    pass
            self._finish(cur.skip_reason)
            try:
                await self.engine.command("stop")
            except EngineError:
                pass
        self.kick()

    async def vote_skip(self, user_id: int) -> dict:
        cur = self.current
        if not cur:
            return {"skipped": False}
        if cur.pending.user_id == user_id and self.settings.get("owner_can_skip"):
            await self.skip(user_id)
            self._toast("toast.skipped_by_owner", name=self.user_name(user_id), title=cur.track.title)
            return {"skipped": True}
        self.votes.vote(user_id)
        self.changed()
        return {"skipped": await self._check_votes()}

    def unvote(self, user_id: int) -> None:
        self.votes.unvote(user_id)
        self.changed()

    async def _check_votes(self) -> bool:
        cur = self.current
        if not cur or not self.votes.voters:
            return False
        listeners = len(self.svc.hub.listener_ids() | self.votes.voters)
        if self.votes.passed(listeners, self.settings.get("skip_mode"), self.settings.get("skip_value")):
            title = cur.track.title
            await self.skip(None, reason="voted")
            self._toast("toast.vote_skipped", title=title)
            return True
        return False

    def on_listeners_change(self) -> None:
        self.changed()
        asyncio.create_task(self._check_votes())

    async def pause(self) -> None:
        if not self.paused:
            self.paused_since = time.time()
        self.paused = True
        self.paused_by_schedule = False
        if self.current:
            await self.engine.set("pause", True)
            self.position = await self.engine.get("time-pos", self.position) or self.position
        self.changed()

    async def resume_playback(self) -> None:
        if self.paused and self._is_stale(self.paused_since):
            await self.stop_all(clear_queue=True, keep_silent=False)  # play fresh music, not yesterday's
        self.paused, self.paused_since = False, None
        if self.stopped:
            self._set_stopped(False)
        if self.current:
            await self.engine.set("pause", False)
            self.position_at = time.time()
        self.changed()
        self.kick()

    async def seek(self, seconds: float) -> None:
        cur = self.current
        if not cur or cur.track.live:
            return
        seconds = max(0.0, min(seconds, (self.duration or seconds) - 1))
        await self.engine.set("time-pos", seconds)
        self.position, self.position_at = seconds, time.time()
        self.changed()

    def volume_allowed(self, user_id: int, is_admin: bool) -> float:
        """Seconds until this user may change the volume again (0 = now)."""
        if is_admin:
            return 0.0
        wait = float(self.settings.get("volume_cooldown_s")) - (time.time() - self._last_volume_change.get(user_id, 0))
        return max(0.0, wait)

    async def set_volume(self, value: int, user_id: int) -> int:
        value = max(0, min(int(value), int(self.settings.get("max_volume"))))
        self._last_volume_change[user_id] = time.time()
        self.settings.set("volume", value)
        if self.engine.alive:
            await self.engine.set("volume", value)
        self.changed()
        return value

    async def apply_max_volume(self) -> None:
        if self.volume > int(self.settings.get("max_volume")):
            await self.set_volume(int(self.settings.get("max_volume")), 0)

    def _set_stopped(self, value: bool) -> None:
        self.stopped = value
        self.settings.set("player_stopped", value)

    async def stop_all(self, clear_queue: bool = True, keep_silent: bool = True) -> None:
        """Stop the music for good: end the current song, forget anything paused or
        prepared and (optionally) empty the queue. With ``keep_silent`` nothing, not
        even autoplay, starts again until someone adds a song or presses play."""
        async with self._lock:
            if self._advance_task and not self._advance_task.done():
                self._advance_task.cancel()
            self.loading = None
            if self.current:
                self._finish("stopped")
            self.preloaded = None
            self._resume = None
            self._ready.clear()
            self.autoplay_buffer.clear()
            if clear_queue:
                self.queue.clear()
            self.paused = self.paused_by_schedule = False
            self.paused_since = None
            self.muted = False
            self._set_stopped(keep_silent)
            if self.settings.get("player_resume"):
                self.settings.reset("player_resume")
            if self.engine.alive:
                try:
                    await self.engine.command("stop")
                    await self.engine.set("mute", False)
                except EngineError:
                    pass
            self.changed()

    async def panic(self, on: bool) -> None:
        """Admin "mute all / pause all"."""
        self.muted = on
        if self.engine.alive:
            await self.engine.set("mute", on)
        if on:
            await self.pause()
        else:
            await self.resume_playback()

    def toggle_like(self, user_id: int) -> bool:
        cur = self.current
        if not cur:
            return False
        if user_id in cur.likes:
            cur.likes.discard(user_id)
            liked = False
        else:
            cur.likes.add(user_id)
            liked = True
            self.svc.hub.broadcast({"type": "reaction", "emoji": "❤", "name": self.user_name(user_id)})
        self.changed()
        return liked

    async def test_tone(self) -> None:
        async with self._lock:
            cur = self.current
            if cur and not cur.test:
                self._resume = Pending(cur.pending.track, cur.pending.user_id, cur.pending.autoplay,
                                       resume_at=self.live_position())
                self._ready[cur.pending.track.key] = (cur.track, cur.playable, time.time())
                if cur.play_id:
                    self.svc.db.execute("DELETE FROM history WHERE id=?", (cur.play_id,))
            self.preloaded = None
            mpv_id = await self.engine.loadfile(TEST_TONE, "replace", {"replaygain": "no"})
            await self.engine.set("pause", False)
            tone = Track(source="system", id="tone", title="Test tone (440 Hz)", duration=3)
            self.current = Current(Pending(tone, None, autoplay=True), tone, Playable(target=TEST_TONE), None,
                                   mpv_id, time.time(), test=True)
            self.paused = False
            self.changed()

    async def change_device(self, device: str | None) -> None:
        if device:
            self.settings.set("audio_device", device)
        else:
            self.settings.reset("audio_device")
        self.engine.device = self.audio_device()
        self.audio_fault = None
        await self.engine.restart()  # supervisor restarts mpv; _on_engine_restart resumes
