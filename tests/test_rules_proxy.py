"""Quiet hours, blocklist, reverse-proxy handling and identity-header trust."""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from starlette.datastructures import Headers

from jukebox.auth import Auth, clean_name
from jukebox.config import load_config
from jukebox.db import Database
from jukebox.models import Track
from jukebox.proxy import ProxyMiddleware
from jukebox.rules import Blocklist, schedule_allows
from jukebox.settings import Secrets, Settings

OFFICE = [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "18:00"}]
NIGHT = [{"days": [4], "start": "22:00", "end": "02:00"}]  # Friday night party


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm)


def test_schedule_office_hours():
    assert schedule_allows(OFFICE, at(2026, 10, 12, 9, 0))      # Monday 09:00
    assert not schedule_allows(OFFICE, at(2026, 10, 12, 18, 0))  # end is exclusive
    assert not schedule_allows(OFFICE, at(2026, 10, 12, 8, 59))
    assert not schedule_allows(OFFICE, at(2026, 10, 17, 12, 0))  # Saturday


def test_schedule_crosses_midnight():
    assert schedule_allows(NIGHT, at(2026, 10, 16, 23, 0))       # Friday 23:00
    assert schedule_allows(NIGHT, at(2026, 10, 17, 1, 30))       # Saturday 01:30, window started Friday
    assert not schedule_allows(NIGHT, at(2026, 10, 17, 2, 0))
    assert not schedule_allows(NIGHT, at(2026, 10, 15, 23, 0))   # Thursday


def test_blocklist(tmp_path):
    bl = Blocklist(Database(tmp_path / "b.db"))
    bl.add("artist", "Baby Shark")
    bl.add("keyword", "10 hours")
    bl.add("track", "youtube:abc")
    assert bl.reason(Track(source="local", id="1", title="Song", artist="Pinkfong & Baby Shark"))
    assert bl.reason(Track(source="youtube", id="x", title="Rain sounds 10 HOURS"))
    assert bl.reason(Track(source="youtube", id="abc", title="Anything"))
    sub = Track(source="youtube", id="zzz", title="ok", substituted_from={"key": "youtube:abc"})
    assert bl.reason(sub)
    assert bl.reason(Track(source="local", id="2", title="Fine song", artist="Someone")) is None


def test_names_are_validated():
    assert clean_name("  Ana   María ") == "Ana María"
    assert clean_name("a\nb") == "a b"  # whitespace is normalised, not rejected
    for bad in ["", "x" * 40, "<script>", "a;b"]:
        with pytest.raises(ValueError):
            clean_name(bad)


# --- proxy middleware -----------------------------------------------------------
def make_config(monkeypatch, **env):
    for k in ("TRUSTED_PROXIES", "BASE_PATH", "AUTH_HEADER", "ADMIN_USERS", "ADMIN_GROUPS", "AUTH_GROUPS_HEADER"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return load_config()


def run_mw(cfg, path="/api/state", client="10.0.0.5", headers=None):
    seen = {}
    sent = []

    async def app(scope, receive, send):
        seen.update(scope)

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "path": path, "raw_path": path.encode(), "client": (client, 1234),
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()], "scheme": "http"}
    asyncio.run(ProxyMiddleware(app, cfg)(scope, None, send))
    return seen, sent


def test_forwarded_headers_only_from_trusted_proxy(monkeypatch):
    cfg = make_config(monkeypatch, TRUSTED_PROXIES="10.0.0.1/32")
    hdrs = {"X-Forwarded-For": "192.168.1.20", "X-Forwarded-Proto": "https"}
    seen, _ = run_mw(cfg, client="10.0.0.1", headers=hdrs)
    assert seen["client"][0] == "192.168.1.20" and seen["scheme"] == "https"
    assert seen["state"]["via_trusted_proxy"]
    seen, _ = run_mw(cfg, client="192.168.1.99", headers=hdrs)  # a LAN client faking headers
    assert seen["client"][0] == "192.168.1.99" and seen["scheme"] == "http"
    assert not seen["state"]["via_trusted_proxy"]


def test_base_path_is_stripped_and_root_still_works(monkeypatch):
    cfg = make_config(monkeypatch, BASE_PATH="/jukebox")
    seen, _ = run_mw(cfg, path="/jukebox/api/state")
    assert seen["path"] == "/api/state" and seen["state"]["public_prefix"] == "/jukebox"
    seen, _ = run_mw(cfg, path="/api/state")
    assert seen["path"] == "/api/state"
    seen, sent = run_mw(cfg, path="/jukebox")
    assert not seen and sent[0]["status"] == 308


def test_identity_header_needs_trusted_peer(monkeypatch, tmp_path):
    cfg = make_config(monkeypatch, TRUSTED_PROXIES="10.0.0.1", AUTH_HEADER="Remote-User",
                      AUTH_GROUPS_HEADER="Remote-Groups", ADMIN_GROUPS="dj-admins", ADMIN_PASSWORD="secret-pass")
    cfg.data_dir = tmp_path
    db = Database(tmp_path / "a.db")
    auth = Auth(cfg, db, Settings(db, {}, Secrets(tmp_path, "")))
    headers = Headers({"remote-user": "carlos", "remote-groups": "staff, dj-admins"})
    ident = auth.resolve(headers, {}, {"via_trusted_proxy": True})
    assert ident and ident.name == "carlos" and ident.is_admin and ident.kind == "proxy"
    # same header straight from a LAN client is ignored
    assert auth.resolve(headers, {}, {"via_trusted_proxy": False}) is None
    # stable user record
    assert auth.resolve(headers, {}, {"via_trusted_proxy": True}).user_id == ident.user_id


def test_admin_password_and_sessions(monkeypatch, tmp_path):
    cfg = make_config(monkeypatch)
    cfg.data_dir = tmp_path
    cfg.admin_password = ""
    db = Database(tmp_path / "a.db")
    auth = Auth(cfg, db, Settings(db, {}, Secrets(tmp_path, "")))
    generated = Path(tmp_path / "admin-password.txt").read_text().strip()
    assert (tmp_path / "admin-password.txt").stat().st_mode & 0o077 == 0
    assert auth.check_password(generated) and not auth.check_password("nope")
    ident, token = auth.create_guest("Ana")
    again = auth.resolve({}, {"jukebox_session": token}, {})
    assert again.user_id == ident.user_id and not again.is_admin
    auth.elevate(again)
    assert auth.resolve({}, {"jukebox_session": token}, {}).is_admin
    assert auth.resolve({}, {"jukebox_session": "forged"}, {}) is None


def test_provider_secrets_are_encrypted_at_rest(tmp_path):
    db = Database(tmp_path / "s.db")
    s = Settings(db, {}, Secrets(tmp_path, ""))
    s.set_provider_config("jamendo", {"client_id": "abc123secret", "enabled": True}, {"client_id"})
    raw = db.scalar("SELECT value FROM settings WHERE key='provider.jamendo'")
    assert "abc123secret" not in raw and "enc:" in raw
    assert Settings(db, {}, Secrets(tmp_path, "")).provider_config("jamendo")["client_id"] == "abc123secret"
