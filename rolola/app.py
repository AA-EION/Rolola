"""HTTP + WebSocket API and static web UI."""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Query, Request, WebSocket
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .auth import COOKIE, LEGACY_COOKIE, Identity
from .config import Config, load_config
from .db import dumps, loads
from .engine import alsa_cards, snd_access
from .housekeeping import cleanup
from .hub import Client
from .logs import RING, get_logger, setup_logging
from .models import Track
from .providers.base import ProviderError
from .proxy import ProxyMiddleware
from .queue import QueueError
from .services import Services
from .settings import DEFAULTS

log = get_logger("app")
WEB_DIR = Path(__file__).parent / "web"
STARTED = time.time()


class ApiError(Exception):
    def __init__(self, code: str, status: int = 400, **params):
        self.code, self.status, self.params = code, status, params


# ---------------------------------------------------------------- request models
class JoinBody(BaseModel):
    name: str = Field(min_length=1, max_length=32)


class PasswordBody(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class AddBody(BaseModel):
    track: Track | None = None
    tracks: list[Track] | None = Field(default=None, max_length=200)


class ExpandBody(BaseModel):
    track: Track


class MoveBody(BaseModel):
    action: Literal["up", "down", "top", "unpin"]


class SeekBody(BaseModel):
    position: float = Field(ge=0, le=86400)


class VolumeBody(BaseModel):
    volume: int = Field(ge=0, le=100)


class PlaylistBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    shared: bool = False


class PlaylistPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    shared: bool | None = None


class TrackBody(BaseModel):
    track: Track


class SettingsBody(BaseModel):
    values: dict[str, Any]
    reset: list[str] = Field(default_factory=list)


class ProviderBody(BaseModel):
    values: dict[str, Any]


class BlockBody(BaseModel):
    kind: Literal["track", "artist", "keyword"]
    value: str = Field(min_length=1, max_length=300)


class UserPatch(BaseModel):
    role: Literal["user", "admin"] | None = None
    banned: bool | None = None


class TokenBody(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    admin: bool = False


class StationBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=2048)
    tags: str | None = Field(default=None, max_length=200)
    favicon: str | None = Field(default=None, max_length=2048)


class DeviceBody(BaseModel):
    device: str | None = Field(default=None, max_length=200)


class MatchBody(BaseModel):
    wanted_key: str = Field(max_length=2100)
    track: Track


class PanicBody(BaseModel):
    on: bool


# ---------------------------------------------------------------- app factory
def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    setup_logging(config.log_level, config.log_json)
    svc = Services(config)
    player = svc.player
    svc.hub.set_state_builder(player.snapshot)
    svc.hub.on_listeners_change = player.on_listeners_change
    background: list[asyncio.Task] = []

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        cleanup(svc.db, config.guest_expire_days, config.history_days, vacuum=True)  # before the library scan starts
        for p in svc.providers.values():
            if p.enabled:
                try:
                    await p.start()
                except Exception as exc:
                    p.fail(exc)
        await player.start()
        background.append(asyncio.create_task(_periodic()))
        log.info("rolola started", extra={"ctx": {"version": __version__, "port": config.port,
                                                   "base_path": config.base_path or "/"}})
        yield
        for t in background:
            t.cancel()
        await player.stop()
        await svc.close()

    async def _periodic():
        last_scan = time.time()
        last_update = time.time() - 86400 + 600  # first yt-dlp update 10 min after start
        last_cleanup = last_vacuum = time.time()
        last_touch = 0.0
        while True:
            await asyncio.sleep(60)
            try:
                if config.library_scan_interval_min > 0 and time.time() - last_scan > config.library_scan_interval_min * 60:
                    last_scan = time.time()
                    local = svc.provider("local")
                    if local and local.enabled:
                        asyncio.create_task(local.rescan())
                if config.ytdlp_auto_update and time.time() - last_update > 86400:
                    last_update = time.time()
                    asyncio.create_task(svc.ytdlp.update())
                if time.time() - last_touch > 300:
                    last_touch = time.time()
                    svc.auth.touch_online([c.ident for c in svc.hub.clients if c.ident])
                if time.time() - last_cleanup > 3600:
                    last_cleanup = time.time()
                    vacuum = time.time() - last_vacuum > 86400
                    if vacuum:
                        last_vacuum = time.time()
                    cleanup(svc.db, config.guest_expire_days, config.history_days, vacuum=vacuum)
            except Exception:
                log.exception("periodic task failed")

    app = FastAPI(title="Rolola API", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
    app.state.svc = svc

    # ------------------------------------------------------------ errors
    @app.exception_handler(ApiError)
    async def _api_error(_req, exc: ApiError):
        return JSONResponse({"error": exc.code, "params": exc.params}, status_code=exc.status)

    @app.exception_handler(QueueError)
    async def _queue_error(_req, exc: QueueError):
        return JSONResponse({"error": exc.code, "params": exc.params}, status_code=400)

    @app.exception_handler(ProviderError)
    async def _provider_error(_req, exc: ProviderError):
        return JSONResponse({"error": str(exc), "status": exc.status}, status_code=502)

    @app.exception_handler(ValueError)
    async def _value_error(_req, exc: ValueError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    # ------------------------------------------------------------ dependencies
    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "?"

    def identity(request: Request) -> Identity | None:
        return svc.auth.resolve(request.headers, request.cookies, request.scope.get("state") or {})

    def check_csrf(request: Request, ident: Identity | None) -> None:
        # Cookie sessions must send a custom header: browsers can't add it cross-site
        # without a CORS preflight, which we never allow.
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and ident and ident.session_hash:
            if "1" not in (request.headers.get("x-rolola"), request.headers.get("x-jukebox")):
                raise ApiError("err.csrf", 403)

    def rate(scope: str, rate_n: float, per: float):
        def dep(request: Request):
            if not svc.ratelimit.allow(scope, client_ip(request), rate_n, per):
                raise ApiError("err.rate_limited", 429)
        return Depends(dep)

    def user(request: Request) -> Identity:
        ident = identity(request)
        if not ident:
            raise ApiError("err.join_first", 401)
        check_csrf(request, ident)
        if request.method != "GET" and not svc.ratelimit.allow("write", f"u{ident.user_id}", 40, 20):
            raise ApiError("err.rate_limited", 429)
        return ident

    def active_user(ident: Identity = Depends(user)) -> Identity:
        if ident.banned:
            raise ApiError("err.banned", 403)
        return ident

    def admin(ident: Identity = Depends(user)) -> Identity:
        if not ident.is_admin:
            raise ApiError("err.admin_only", 403)
        return ident

    def cookie_path(request: Request) -> str:
        prefix = (request.scope.get("state") or {}).get("public_prefix") or ""
        return (prefix or "") + "/"

    def secure_cookie(request: Request) -> bool:
        return request.url.scheme == "https"

    # ------------------------------------------------------------ health
    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"ok": True, "engine": player.engine.alive, "status": player.status(),
                "audio_fault": player.audio_fault, "uptime": round(time.time() - STARTED)}

    @app.get("/readyz", include_in_schema=False)
    async def readyz():
        ok = player.engine.alive
        return JSONResponse({"ready": ok}, status_code=200 if ok else 503)

    # ------------------------------------------------------------ session
    @app.get("/api/me", tags=["session"])
    async def me(request: Request):
        ident = identity(request)
        resp = JSONResponse({"user": ident.public() if ident else None,
                             "proxy_auth": bool(config.auth_header),
                             "admin_password_from_env": svc.auth.password_from_env})
        token = request.cookies.get(COOKIE) or request.cookies.get(LEGACY_COOKIE)
        if ident and ident.session_hash and token:  # sliding expiry: every visit renews it
            resp.set_cookie(COOKIE, token, max_age=config.guest_expire_days * 86400, httponly=True,
                            samesite="lax", path=cookie_path(request), secure=secure_cookie(request))
            if request.cookies.get(LEGACY_COOKIE):
                resp.delete_cookie(LEGACY_COOKIE, path=cookie_path(request))
        return resp

    @app.post("/api/session/join", tags=["session"], dependencies=[rate("join", 5, 60)])
    async def join(body: JoinBody, request: Request):
        current = identity(request)
        if current and current.kind != "guest":
            raise ApiError("err.already_joined")
        if current:  # switching names on this device: sign the old one out first
            svc.auth.logout(current)
        ident, token = svc.auth.create_guest(body.name)
        resp = JSONResponse({"user": ident.public()})
        resp.set_cookie(COOKIE, token, max_age=config.guest_expire_days * 86400, httponly=True, samesite="lax",
                        path=cookie_path(request), secure=secure_cookie(request))
        return resp

    @app.post("/api/session/rename", tags=["session"])
    async def rename(body: JoinBody, ident: Identity = Depends(user)):
        svc.auth.rename(ident, body.name)
        player.forget_user_name(ident.user_id)
        player.changed()
        return {"ok": True}

    @app.post("/api/session/logout", tags=["session"])
    async def logout(request: Request, ident: Identity = Depends(user)):
        svc.auth.logout(ident)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path=cookie_path(request))
        return resp

    @app.post("/api/session/admin", tags=["session"], dependencies=[rate("admin-login", 5, 300)])
    async def admin_login(body: PasswordBody, ident: Identity = Depends(user)):
        if ident.kind != "guest":
            raise ApiError("err.admin_via_proxy", 400)
        if not svc.auth.check_password(body.password):
            log.warning("failed admin login", extra={"ctx": {"user": ident.user_id}})
            raise ApiError("err.bad_password", 403)
        svc.auth.elevate(ident)
        log.info("admin login", extra={"ctx": {"user": ident.user_id, "name": ident.name}})
        return {"ok": True}

    @app.post("/api/session/admin/logout", tags=["session"])
    async def admin_logout(ident: Identity = Depends(user)):
        svc.auth.drop_admin(ident)
        return {"ok": True}

    # ------------------------------------------------------------ state
    @app.get("/api/state", tags=["player"])
    async def state(request: Request):
        if not identity(request) and not svc.settings.get("public_display"):
            raise ApiError("err.join_first", 401)
        return player.snapshot()

    @app.get("/api/sources", tags=["search"])
    async def sources():
        return [p.info() for p in svc.providers.values() if p.enabled]

    # ------------------------------------------------------------ search
    async def _search_one(pid: str, q: str, limit: int, is_admin: bool = False) -> dict:
        prov = svc.provider(pid)
        if not prov or not prov.enabled:
            return {"source": pid, "results": [], "error": "Source disabled."}
        url_mode = q.startswith(("http://", "https://", "spotify:"))
        if url_mode and not prov.owns_url(q) and pid != "url":
            return {"source": pid, "results": []}
        if not url_mode and "search" not in prov.capabilities:
            return {"source": pid, "results": []}
        try:
            results = await asyncio.wait_for(prov.search(q, limit), 30)
        except asyncio.TimeoutError:
            return {"source": pid, "results": [], "error": "The source took too long to answer."}
        except ProviderError as exc:
            return {"source": pid, "results": [], "error": str(exc)}
        except Exception:
            log.exception("search failed on %s", pid)
            return {"source": pid, "results": [], "error": "Unexpected error."}
        max_s = int(svc.settings.get("max_track_minutes")) * 60
        out = []
        for t in results:
            d = t.public()
            d["blocked"] = bool(svc.blocklist.reason(t))
            d["too_long"] = bool(max_s and not is_admin and not t.live and not t.is_container
                                 and t.duration and t.duration > max_s)
            out.append(d)
        out.sort(key=lambda d: d["too_long"])  # stable: keeps the source's ranking otherwise
        return {"source": pid, "results": out}

    @app.get("/api/search", tags=["search"])
    async def search(q: str = Query(min_length=1, max_length=500), source: str = Query(max_length=32),
                     limit: int = Query(20, ge=1, le=50), ident: Identity = Depends(user)):
        if not svc.ratelimit.allow("search", f"u{ident.user_id}", 30, 30):
            raise ApiError("err.rate_limited", 429)
        q = q.strip()
        if q.startswith(("http://", "https://", "spotify:")):
            owner = next((p.id for p in svc.enabled_providers() if p.owns_url(q)), "url")
            if source != owner:
                return {"source": source, "results": []}
            return await _search_one(owner, q, limit, ident.is_admin)
        return await _search_one(source, q, limit, ident.is_admin)

    @app.get("/api/search/all", tags=["search"])
    async def search_all(q: str = Query(min_length=1, max_length=500), limit: int = Query(10, ge=1, le=30),
                         ident: Identity = Depends(user)):
        if not svc.ratelimit.allow("search", f"u{ident.user_id}", 30, 30):
            raise ApiError("err.rate_limited", 429)
        ids = [p.id for p in svc.enabled_providers() if "search" in p.capabilities]
        groups = await asyncio.gather(*(_search_one(pid, q.strip(), limit, ident.is_admin) for pid in ids))
        return {"groups": groups}

    @app.post("/api/expand", tags=["search"])
    async def expand(body: ExpandBody, ident: Identity = Depends(user)):
        prov = svc.provider(body.track.source)
        if not prov or not prov.enabled:
            raise ApiError("err.source_disabled")
        tracks = await asyncio.wait_for(prov.expand(body.track), 45)
        return {"tracks": [t.public() for t in tracks]}

    # ------------------------------------------------------------ queue
    def _validate_for_queue(tracks: list[Track], ident: Identity) -> tuple[list[Track], list[str]]:
        max_s = int(svc.settings.get("max_track_minutes")) * 60
        queued = {i.track.key for i in player.queue.ordered()}
        for p in (player.current.pending if player.current else None, player.loading):
            if p:
                queued.add(p.track.key)
        ok, skipped = [], []
        for t in tracks:
            if t.key in queued:
                skipped.append(f"{t.title}: already in the queue")
                continue
            queued.add(t.key)
            prov = svc.provider(t.source)
            if not prov or not (prov.enabled or svc.settings.get("substitute_unavailable")):
                skipped.append(f"{t.title}: source disabled")
                continue
            if (reason := svc.blocklist.reason(t)):
                skipped.append(f"{t.title}: blocked ({reason})")
                continue
            if t.duration and max_s and t.duration > max_s and not ident.is_admin and not t.live:
                skipped.append(f"{t.title}: longer than {max_s // 60} min")
                continue
            ok.append(t)
        return ok, skipped

    @app.post("/api/queue", tags=["queue"])
    async def add_to_queue(body: AddBody, ident: Identity = Depends(active_user)):
        tracks = list(body.tracks or []) + ([body.track] if body.track else [])
        if not tracks:
            raise ApiError("err.bad_request")
        expanded: list[Track] = []
        for t in tracks:
            if t.is_container:
                prov = svc.provider(t.source)
                if not prov or not prov.enabled:
                    raise ApiError("err.source_disabled")
                expanded += await asyncio.wait_for(prov.expand(t), 45)
            else:
                expanded.append(t)
        ok, skipped = _validate_for_queue(expanded[:500], ident)
        if not ok:
            raise ApiError("err.nothing_added", 400, reasons=skipped[:5])
        added, dropped = player.queue.add(ok, ident.user_id, ident.is_admin)
        player.on_tracks_added(added)
        position = None
        order = [i.id for i in player.queue.ordered()]
        if added and added[0].id in order:
            position = order.index(added[0].id) + 1
        return {"added": len(added), "dropped_by_limit": dropped, "skipped": skipped[:10], "position": position,
                "ids": [a.id for a in added]}

    @app.delete("/api/queue/{item_id}", tags=["queue"])
    async def remove_from_queue(item_id: int, ident: Identity = Depends(user)):
        player.queue.remove(item_id, ident.user_id, ident.is_admin)
        return {"ok": True}

    @app.post("/api/queue/{item_id}/move", tags=["queue"])
    async def move_in_queue(item_id: int, body: MoveBody, ident: Identity = Depends(user)):
        player.queue.move(item_id, body.action, ident.user_id, ident.is_admin)
        return {"ok": True}

    @app.delete("/api/queue", tags=["queue"])
    async def clear_my_queue(mine: bool = True, ident: Identity = Depends(user)):
        if not mine and not ident.is_admin:
            raise ApiError("err.admin_only", 403)
        n = player.queue.clear(ident.user_id if mine else None)
        return {"removed": n}

    # ------------------------------------------------------------ player controls
    @app.post("/api/player/skip", tags=["player"])
    async def skip(ident: Identity = Depends(active_user)):
        cur = player.current
        if not cur:
            return {"skipped": False}
        if ident.is_admin:
            await player.skip(ident.user_id, force=True)
            return {"skipped": True}
        return await player.vote_skip(ident.user_id)

    @app.post("/api/player/vote", tags=["player"])
    async def vote(ident: Identity = Depends(active_user)):
        return await player.vote_skip(ident.user_id)

    @app.delete("/api/player/vote", tags=["player"])
    async def unvote(ident: Identity = Depends(active_user)):
        player.unvote(ident.user_id)
        return {"ok": True}

    @app.post("/api/player/pause", tags=["player"])
    async def pause(ident: Identity = Depends(active_user)):
        if not (ident.is_admin or svc.settings.get("user_can_pause")):
            raise ApiError("err.admin_only", 403)
        await player.pause()
        return {"ok": True}

    @app.post("/api/player/play", tags=["player"])
    async def play(ident: Identity = Depends(active_user)):
        if not (ident.is_admin or svc.settings.get("user_can_pause")):
            raise ApiError("err.admin_only", 403)
        await player.resume_playback()
        return {"ok": True}

    @app.post("/api/player/seek", tags=["player"])
    async def seek(body: SeekBody, ident: Identity = Depends(active_user)):
        cur = player.current
        own = cur and cur.pending.user_id == ident.user_id
        if not (ident.is_admin or svc.settings.get("user_can_seek") or own):
            raise ApiError("err.admin_only", 403)
        await player.seek(body.position)
        return {"ok": True}

    @app.post("/api/player/volume", tags=["player"])
    async def volume(body: VolumeBody, ident: Identity = Depends(active_user)):
        if not (ident.is_admin or svc.settings.get("user_can_volume")):
            raise ApiError("err.admin_only", 403)
        wait = player.volume_allowed(ident.user_id, ident.is_admin)
        if wait > 0:
            raise ApiError("err.volume_cooldown", 429, seconds=int(wait + 0.99))
        v = await player.set_volume(body.volume, ident.user_id)
        return {"volume": v, "max": svc.settings.get("max_volume")}

    @app.post("/api/player/like", tags=["player"])
    async def like(ident: Identity = Depends(active_user)):
        return {"liked": player.toggle_like(ident.user_id)}

    @app.get("/api/lyrics", tags=["player"])
    async def lyrics(request: Request):
        cur = player.current
        if not svc.settings.get("lyrics") or not cur or cur.test or cur.track.live:
            return {"lyrics": None}
        if not identity(request) and not svc.settings.get("public_display"):
            raise ApiError("err.join_first", 401)
        data = await svc.lyrics.get(cur.track)
        return {"play_id": cur.play_id, "lyrics": data}

    # ------------------------------------------------------------ matches
    @app.get("/api/match", tags=["queue"])
    async def match_alternatives(key: str = Query(max_length=2100), ident: Identity = Depends(user)):
        hit = svc.matcher.cached(key)
        if not hit:
            return {"match": None, "alternatives": []}
        return {"match": hit[0].public(), "alternatives": [a.public() for a in hit[1]]}

    @app.post("/api/match", tags=["queue"])
    async def choose_match(body: MatchBody, ident: Identity = Depends(active_user)):
        svc.matcher.choose(body.wanted_key, body.track)
        return {"ok": True}

    # ------------------------------------------------------------ library
    def _history_rows(where: str, params: tuple, limit: int) -> list[dict]:
        rows = svc.db.all(
            f"SELECT h.*, u.name AS user_name FROM history h LEFT JOIN users u ON u.id=h.user_id {where} "
            "ORDER BY h.started_at DESC LIMIT ?", (*params, limit))
        return [{"id": r["id"], "track": Track.model_validate(loads(r["track"])).public(), "user": r["user_name"],
                 "user_id": r["user_id"], "autoplay": bool(r["autoplay"]), "started_at": r["started_at"],
                 "outcome": r["outcome"], "error": r["error"], "likes": r["likes"]} for r in rows]

    @app.get("/api/history", tags=["library"])
    async def history(limit: int = Query(50, ge=1, le=200), mine: bool = False, ident: Identity = Depends(user)):
        if mine:
            return _history_rows("WHERE h.user_id=?", (ident.user_id,), limit)
        return _history_rows("", (), limit)

    @app.get("/api/stats/me", tags=["library"])
    async def my_stats(ident: Identity = Depends(user)):
        db = svc.db
        uid = ident.user_id
        return {
            "added": db.scalar("SELECT COUNT(*) FROM history WHERE user_id=?", (uid,)),
            "played": db.scalar("SELECT COUNT(*) FROM history WHERE user_id=? AND outcome='played'", (uid,)),
            "skipped": db.scalar("SELECT COUNT(*) FROM history WHERE user_id=? AND outcome IN ('skipped','voted')", (uid,)),
            "likes_received": db.scalar("SELECT COALESCE(SUM(likes),0) FROM history WHERE user_id=?", (uid,)),
            "top_artists": db.all(
                "SELECT json_extract(track,'$.artist') AS artist, COUNT(*) AS n FROM history WHERE user_id=? "
                "AND json_extract(track,'$.artist') IS NOT NULL GROUP BY artist ORDER BY n DESC LIMIT 5", (uid,)),
        }

    @app.get("/api/favorites", tags=["library"])
    async def favorites(ident: Identity = Depends(user)):
        rows = svc.db.all("SELECT * FROM favorites WHERE user_id=? ORDER BY created_at DESC", (ident.user_id,))
        return [Track.model_validate(loads(r["track"])).public() for r in rows]

    @app.post("/api/favorites", tags=["library"])
    async def add_favorite(body: TrackBody, ident: Identity = Depends(user)):
        svc.db.execute("INSERT OR REPLACE INTO favorites(user_id, track_key, track, created_at) VALUES (?,?,?,?)",
                       (ident.user_id, body.track.key, dumps(body.track.model_dump()), time.time()))
        return {"ok": True}

    @app.delete("/api/favorites", tags=["library"])
    async def remove_favorite(key: str = Query(max_length=2100), ident: Identity = Depends(user)):
        svc.db.execute("DELETE FROM favorites WHERE user_id=? AND track_key=?", (ident.user_id, key))
        return {"ok": True}

    def _playlist(pid: int, ident: Identity, write: bool = False) -> dict:
        pl = svc.db.one("SELECT * FROM playlists WHERE id=?", (pid,))
        if not pl or (not pl["shared"] and pl["owner_id"] != ident.user_id and not ident.is_admin):
            raise ApiError("err.not_found", 404)
        if write and pl["owner_id"] != ident.user_id and not ident.is_admin and not pl["shared"]:
            raise ApiError("err.not_yours", 403)
        return pl

    @app.get("/api/playlists", tags=["library"])
    async def playlists(ident: Identity = Depends(user)):
        rows = svc.db.all(
            "SELECT p.*, u.name AS owner, (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id=p.id) AS n "
            "FROM playlists p LEFT JOIN users u ON u.id=p.owner_id WHERE p.shared=1 OR p.owner_id=? "
            "ORDER BY p.shared DESC, p.name", (ident.user_id,))
        return [{"id": r["id"], "name": r["name"], "shared": bool(r["shared"]), "owner": r["owner"],
                 "mine": r["owner_id"] == ident.user_id, "count": r["n"]} for r in rows]

    @app.post("/api/playlists", tags=["library"])
    async def create_playlist(body: PlaylistBody, ident: Identity = Depends(active_user)):
        pid = svc.db.execute("INSERT INTO playlists(name, owner_id, shared, created_at) VALUES (?,?,?,?)",
                             (body.name.strip(), ident.user_id, int(body.shared), time.time())).lastrowid
        return {"id": pid}

    @app.get("/api/playlists/{pid}", tags=["library"])
    async def get_playlist(pid: int, ident: Identity = Depends(user)):
        pl = _playlist(pid, ident)
        items = svc.db.all("SELECT * FROM playlist_items WHERE playlist_id=? ORDER BY pos", (pid,))
        return {"id": pl["id"], "name": pl["name"], "shared": bool(pl["shared"]),
                "mine": pl["owner_id"] == ident.user_id,
                "items": [{"id": i["id"], "track": Track.model_validate(loads(i["track"])).public()} for i in items]}

    @app.patch("/api/playlists/{pid}", tags=["library"])
    async def patch_playlist(pid: int, body: PlaylistPatch, ident: Identity = Depends(user)):
        pl = _playlist(pid, ident)
        if pl["owner_id"] != ident.user_id and not ident.is_admin:
            raise ApiError("err.not_yours", 403)
        if body.name is not None:
            svc.db.execute("UPDATE playlists SET name=? WHERE id=?", (body.name.strip(), pid))
        if body.shared is not None:
            svc.db.execute("UPDATE playlists SET shared=? WHERE id=?", (int(body.shared), pid))
        return {"ok": True}

    @app.delete("/api/playlists/{pid}", tags=["library"])
    async def delete_playlist(pid: int, ident: Identity = Depends(user)):
        pl = _playlist(pid, ident)
        if pl["owner_id"] != ident.user_id and not ident.is_admin:
            raise ApiError("err.not_yours", 403)
        svc.db.execute("DELETE FROM playlists WHERE id=?", (pid,))
        return {"ok": True}

    @app.post("/api/playlists/{pid}/items", tags=["library"])
    async def add_playlist_item(pid: int, body: AddBody, ident: Identity = Depends(active_user)):
        _playlist(pid, ident, write=True)
        tracks = list(body.tracks or []) + ([body.track] if body.track else [])
        pos = float(svc.db.scalar("SELECT COALESCE(MAX(pos),0) FROM playlist_items WHERE playlist_id=?", (pid,)))
        rows = []
        for t in tracks[:500]:
            pos += 1
            rows.append((pid, t.key, dumps(t.model_dump()), pos, ident.user_id))
        svc.db.executemany("INSERT INTO playlist_items(playlist_id, track_key, track, pos, added_by) VALUES (?,?,?,?,?)", rows)
        return {"added": len(rows)}

    @app.delete("/api/playlists/{pid}/items/{item_id}", tags=["library"])
    async def remove_playlist_item(pid: int, item_id: int, ident: Identity = Depends(user)):
        _playlist(pid, ident, write=True)
        svc.db.execute("DELETE FROM playlist_items WHERE id=? AND playlist_id=?", (item_id, pid))
        return {"ok": True}

    @app.post("/api/playlists/{pid}/enqueue", tags=["library"])
    async def enqueue_playlist(pid: int, shuffle: bool = False, ident: Identity = Depends(active_user)):
        _playlist(pid, ident)
        rows = svc.db.all("SELECT track FROM playlist_items WHERE playlist_id=? ORDER BY pos", (pid,))
        tracks = [Track.model_validate(loads(r["track"])) for r in rows]
        if shuffle:
            import random
            random.shuffle(tracks)
        ok, skipped = _validate_for_queue(tracks, ident)
        if not ok:
            raise ApiError("err.nothing_added", 400, reasons=skipped[:5])
        added, dropped = player.queue.add(ok, ident.user_id, ident.is_admin)
        player.on_tracks_added(added)
        return {"added": len(added), "dropped_by_limit": dropped}

    # ------------------------------------------------------------ art
    @app.get("/api/art/local/{lib_id}", tags=["library"])
    async def local_art(lib_id: int):
        local = svc.provider("local")
        art = await asyncio.get_running_loop().run_in_executor(None, local.art_for, lib_id) if local else None
        if art is None:
            return Response(status_code=404)
        headers = {"Cache-Control": "public, max-age=86400"}
        if isinstance(art, Path):
            return FileResponse(art, headers=headers)
        data, mime = art
        return Response(data, media_type=mime if mime.startswith("image/") else "image/jpeg", headers=headers)

    # ------------------------------------------------------------ admin
    @app.get("/api/admin/settings", tags=["admin"])
    async def get_settings(_: Identity = Depends(admin)):
        return {"settings": svc.settings.all_admin(), "audio_device_env": config.audio_device}

    @app.put("/api/admin/settings", tags=["admin"])
    async def put_settings(body: SettingsBody, ident: Identity = Depends(admin)):
        for key in body.reset:
            if key in DEFAULTS:
                svc.settings.reset(key)
        for key, value in body.values.items():
            if key not in DEFAULTS or key in ("audio_device", "volume"):
                continue
            default = DEFAULTS[key]
            if isinstance(default, bool) and not isinstance(value, bool):
                raise ApiError("err.bad_setting", key=key)
            if isinstance(default, (int, float)) and not isinstance(default, bool):
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ApiError("err.bad_setting", key=key)
            svc.settings.set(key, value)
        if svc.settings.get("max_volume") is not None:
            await player.apply_max_volume()
        log.info("settings changed", extra={"ctx": {"by": ident.name, "keys": ",".join(body.values)}})
        player.changed()
        player.kick()
        return {"ok": True}

    @app.get("/api/admin/providers", tags=["admin"])
    async def admin_providers(_: Identity = Depends(admin)):
        return [p.info(admin=True) for p in svc.providers.values()]

    @app.put("/api/admin/providers/{pid}", tags=["admin"])
    async def put_provider(pid: str, body: ProviderBody, ident: Identity = Depends(admin)):
        prov = svc.provider(pid)
        if not prov or not prov.config_fields:
            raise ApiError("err.not_found", 404)
        allowed = {f.key for f in prov.config_fields}
        values = {k: v for k, v in body.values.items() if k in allowed}
        svc.settings.set_provider_config(pid, values, prov.secret_fields())
        prov.status, prov.status_detail = "connected", ""
        if prov.enabled:
            with contextlib.suppress(Exception):
                await prov.start()
        log.info("provider config changed", extra={"ctx": {"by": ident.name, "provider": pid}})
        return prov.info(admin=True)

    @app.post("/api/admin/providers/{pid}/test", tags=["admin"])
    async def test_provider(pid: str, _: Identity = Depends(admin)):
        prov = svc.provider(pid)
        if not prov:
            raise ApiError("err.not_found", 404)
        try:
            msg = await asyncio.wait_for(prov.test(), 40)
            prov.ok()
            return {"ok": True, "message": msg}
        except Exception as exc:
            prov.fail(exc)
            return {"ok": False, "message": str(exc) or "Failed."}

    @app.post("/api/admin/library/rescan", tags=["admin"])
    async def rescan(_: Identity = Depends(admin)):
        local = svc.provider("local")
        asyncio.create_task(local.rescan())
        return {"started": True}

    @app.post("/api/admin/ytdlp/update", tags=["admin"])
    async def ytdlp_update(_: Identity = Depends(admin)):
        return {"result": await svc.ytdlp.update()}

    @app.post("/api/admin/ytdlp/rollback", tags=["admin"])
    async def ytdlp_rollback(_: Identity = Depends(admin)):
        await svc.ytdlp.rollback()
        return {"version": await svc.ytdlp.version()}

    @app.get("/api/admin/blocklist", tags=["admin"])
    async def get_blocklist(_: Identity = Depends(admin)):
        return svc.blocklist.all()

    @app.post("/api/admin/blocklist", tags=["admin"])
    async def add_block(body: BlockBody, _: Identity = Depends(admin)):
        svc.blocklist.add(body.kind, body.value)
        # drop already-queued matches
        for it in player.queue.ordered():
            if svc.blocklist.reason(it.track):
                player.queue.take(it.id)
        return {"ok": True}

    @app.delete("/api/admin/blocklist/{rule_id}", tags=["admin"])
    async def remove_block(rule_id: int, _: Identity = Depends(admin)):
        svc.blocklist.remove(rule_id)
        return {"ok": True}

    @app.get("/api/admin/users", tags=["admin"])
    async def list_users(_: Identity = Depends(admin)):
        online = svc.hub.listener_ids()
        rows = svc.db.all(
            "SELECT u.*, (SELECT COUNT(*) FROM queue q WHERE q.user_id=u.id) AS queued, "
            "(SELECT COUNT(*) FROM history h WHERE h.user_id=u.id) AS played FROM users u "
            "ORDER BY u.last_seen DESC LIMIT 500")
        return [{"id": r["id"], "name": r["name"], "kind": r["kind"], "role": r["role"], "banned": bool(r["banned"]),
                 "last_seen": r["last_seen"], "queued": r["queued"], "played": r["played"],
                 "online": r["id"] in online} for r in rows]

    @app.patch("/api/admin/users/{uid}", tags=["admin"])
    async def patch_user(uid: int, body: UserPatch, ident: Identity = Depends(admin)):
        if body.role is not None:
            svc.db.execute("UPDATE users SET role=? WHERE id=?", (body.role, uid))
        if body.banned is not None:
            svc.db.execute("UPDATE users SET banned=? WHERE id=?", (int(body.banned), uid))
            if body.banned:
                player.queue.clear(uid)
        log.info("user changed", extra={"ctx": {"by": ident.name, "user": uid}})
        return {"ok": True}

    @app.get("/api/admin/tokens", tags=["admin"])
    async def list_tokens(_: Identity = Depends(admin)):
        return svc.db.all("SELECT id, name, is_admin, created_at, last_used FROM api_tokens ORDER BY created_at DESC")

    @app.post("/api/admin/tokens", tags=["admin"])
    async def create_token(body: TokenBody, _: Identity = Depends(admin)):
        return {"token": svc.auth.create_api_token(body.name, body.admin)}

    @app.delete("/api/admin/tokens/{tid}", tags=["admin"])
    async def delete_token(tid: int, _: Identity = Depends(admin)):
        row = svc.db.one("SELECT user_id FROM api_tokens WHERE id=?", (tid,))
        svc.db.execute("DELETE FROM api_tokens WHERE id=?", (tid,))
        return {"ok": bool(row)}

    @app.get("/api/admin/stations", tags=["admin"])
    async def list_stations(_: Identity = Depends(admin)):
        return svc.db.all("SELECT * FROM stations ORDER BY name")

    @app.post("/api/admin/stations", tags=["admin"])
    async def add_station(body: StationBody, _: Identity = Depends(admin)):
        if not body.url.startswith(("http://", "https://")):
            raise ApiError("err.bad_url")
        sid = svc.db.execute("INSERT INTO stations(name, url, tags, favicon, created_at) VALUES (?,?,?,?,?)",
                             (body.name, body.url, body.tags, body.favicon, time.time())).lastrowid
        return {"id": sid}

    @app.delete("/api/admin/stations/{sid}", tags=["admin"])
    async def delete_station(sid: int, _: Identity = Depends(admin)):
        svc.db.execute("DELETE FROM stations WHERE id=?", (sid,))
        return {"ok": True}

    @app.post("/api/admin/password", tags=["admin"])
    async def change_password(body: PasswordBody, _: Identity = Depends(admin)):
        svc.auth.change_password(body.password)
        return {"ok": True}

    @app.post("/api/admin/panic", tags=["admin"])
    async def panic(body: PanicBody, ident: Identity = Depends(admin)):
        await player.panic(body.on)
        log.warning("panic %s", "ON" if body.on else "off", extra={"ctx": {"by": ident.name}})
        return {"ok": True}

    @app.post("/api/admin/test-tone", tags=["admin"])
    async def test_tone(_: Identity = Depends(admin)):
        await player.test_tone()
        return {"ok": True}

    @app.post("/api/admin/audio-device", tags=["admin"])
    async def set_device(body: DeviceBody, ident: Identity = Depends(admin)):
        await player.change_device(body.device.strip() if body.device else None)
        log.info("audio device changed", extra={"ctx": {"by": ident.name, "device": player.audio_device()}})
        return {"device": player.audio_device()}

    @app.post("/api/admin/cache/clear", tags=["admin"])
    async def clear_cache(_: Identity = Depends(admin)):
        svc.cache.clear()
        return {"ok": True}

    @app.get("/api/admin/logs", tags=["admin"])
    async def logs(limit: int = Query(200, ge=1, le=1000), level: str | None = None, _: Identity = Depends(admin)):
        rows = list(RING)
        if level:
            order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
            lvl = order.index(level.upper()) if level.upper() in order else 0
            rows = [r for r in rows if order.index(r["level"]) >= lvl]
        return rows[-limit:]

    @app.get("/api/admin/diagnostics", tags=["admin"])
    async def diagnostics(_: Identity = Depends(admin)):
        eng = player.engine
        devices = await eng.get("audio-device-list", []) if eng.alive else []
        du = shutil.disk_usage(config.data_dir)
        cache_bytes, cache_files = svc.cache.usage()
        return {
            "version": __version__,
            "uptime_s": round(time.time() - STARTED),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "load": os.getloadavg(),
            "audio": {
                "configured_device": player.audio_device(),
                "env_device": config.audio_device,
                "engine_alive": eng.alive,
                "engine_restarts": eng.restarts,
                "current_ao": await eng.get("current-ao") if eng.alive else None,
                "fault": player.audio_fault,
                "mpv_devices": [d for d in devices if d.get("name", "").startswith("alsa")],
                "alsa_cards": alsa_cards(),
                "dev_snd": snd_access(),
                "mpv_log": eng.stderr_tail[-15:],
                "mpv_version": await _mpv_version(),
            },
            "ytdlp": {"version": await svc.ytdlp.version(), "last_update": svc.ytdlp.last_update,
                      "last_result": svc.ytdlp.last_update_result, "js_runtime": shutil.which("deno") or shutil.which("node")},
            "storage": {"data_dir": str(config.data_dir), "free_mb": du.free // 2**20, "total_mb": du.total // 2**20,
                        "cache_mb": round(cache_bytes / 2**20, 1), "cache_files": cache_files,
                        "cache_max_mb": config.cache_max_mb,
                        "db_mb": round(config.db_path.stat().st_size / 2**20, 2) if config.db_path.exists() else 0},
            "library": {"path": str(config.music_dir), "mounted": config.music_dir.is_dir(),
                        "tracks": svc.db.scalar("SELECT COUNT(*) FROM library"),
                        "last_scan": svc.provider("local").scanner.last_result},
            "providers": [{"id": p.id, "name": p.name, "status": p.current_status()[0], "detail": p.current_status()[1]}
                          for p in svc.providers.values()],
            "clients": len(svc.hub.clients),
            "listeners": len(svc.hub.listener_ids()),
            "proxy": {"trusted_proxies": [str(n) for n in config.trusted_proxies], "auth_header": config.auth_header or None,
                      "base_path": config.base_path or "/"},
        }

    async def _mpv_version() -> str:
        try:
            proc = await asyncio.create_subprocess_exec(config.mpv_binary, "--version", stdout=asyncio.subprocess.PIPE)
            out, _ = await asyncio.wait_for(proc.communicate(), 5)
            return out.decode().splitlines()[0]
        except Exception:
            return "unknown"

    # ------------------------------------------------------------ websocket
    @app.websocket("/api/ws")
    async def ws(websocket: WebSocket, display: bool = False):
        origin = websocket.headers.get("origin")
        host = websocket.headers.get("host")
        if origin and host and origin.split("://", 1)[-1].rstrip("/") != host:
            await websocket.close(code=4403)
            return
        ident = svc.auth.resolve(websocket.headers, websocket.cookies, websocket.scope.get("state") or {})
        if not ident and not svc.settings.get("public_display"):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        client = Client(websocket, ident, display=display or ident is None)
        await svc.hub.serve(client, {"type": "state", "state": player.snapshot(),
                                     "me": ident.public() if ident else None})

    # ------------------------------------------------------------ docs & static
    @app.get("/api/docs", include_in_schema=False)
    async def docs():
        return get_swagger_ui_html(openapi_url="openapi.json", title="Rolola API")

    @app.get("/display", include_in_schema=False)
    async def display_redirect():
        return HTMLResponse('<!doctype html><meta http-equiv="refresh" content="0; url=./#/display">')

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if request.url.path.startswith("/api/") and "cache-control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"
        elif not request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-cache")
        return resp

    wrapped = ProxyMiddleware(app, config)
    wrapped.svc = svc  # handy for tests and tooling
    return wrapped  # type: ignore[return-value]
