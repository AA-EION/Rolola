"""Identity.

Three ways to be someone, checked in this order:

1. Reverse-proxy identity header (``AUTH_HEADER``, e.g. ``Remote-User``),
   accepted *only* when the TCP peer is in ``TRUSTED_PROXIES``.
2. ``Authorization: Bearer <token>`` API tokens created by an admin.
3. A session cookie obtained by choosing a display name.

Admin rights come from ``ADMIN_USERS`` / ``ADMIN_GROUPS`` (proxy identity),
a user's ``admin`` role, an admin API token, or a session elevated with the
admin password.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .db import Database
from .logs import get_logger, register_secret
from .settings import Settings

log = get_logger("auth")
COOKIE = "jukebox_session"
NAME_RE = re.compile(r"^[\w .'@\-]{1,32}$", re.UNICODE)


@dataclass
class Identity:
    user_id: int
    name: str
    is_admin: bool
    kind: str            # guest | proxy | api
    banned: bool = False
    session_hash: str | None = None
    can_logout: bool = True

    def public(self) -> dict:
        return {
            "id": self.user_id,
            "name": self.name,
            "is_admin": self.is_admin,
            "kind": self.kind,
            "banned": self.banned,
            "can_logout": self.can_logout,
        }


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    candidate = hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(candidate, stored)


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not NAME_RE.match(name):
        raise ValueError("err.bad_name")
    return name


class Auth:
    def __init__(self, config: Config, db: Database, settings: Settings):
        self.config = config
        self.db = db
        self.settings = settings
        self._bootstrap_password()

    # --- admin password ---------------------------------------------------
    def _bootstrap_password(self) -> None:
        if self.config.admin_password:
            register_secret(self.config.admin_password)
            self._env_hash = hash_password(self.config.admin_password)
            return
        self._env_hash = None
        if not self.settings.get("admin_password_hash"):
            password = secrets.token_urlsafe(12)
            self.settings.set("admin_password_hash", hash_password(password))
            path = Path(self.config.data_dir) / "admin-password.txt"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(password + "\n")
            log.warning("No ADMIN_PASSWORD set; generated one and wrote it to %s", path)

    @property
    def password_from_env(self) -> bool:
        return self._env_hash is not None

    def check_password(self, password: str) -> bool:
        stored = self._env_hash or self.settings.get("admin_password_hash") or ""
        return bool(password) and verify_password(password, stored)

    def change_password(self, new_password: str) -> None:
        if self.password_from_env:
            raise ValueError("err.password_from_env")
        if len(new_password) < 8:
            raise ValueError("err.password_short")
        self.settings.set("admin_password_hash", hash_password(new_password))
        path = Path(self.config.data_dir) / "admin-password.txt"
        if path.exists():
            path.unlink()

    # --- users / sessions ---------------------------------------------------
    def _touch(self, user_id: int) -> None:
        self.db.execute("UPDATE users SET last_seen=? WHERE id=?", (time.time(), user_id))

    def create_guest(self, name: str) -> tuple[Identity, str]:
        name = clean_name(name)
        now = time.time()
        uid = self.db.execute(
            "INSERT INTO users(name, kind, created_at, last_seen) VALUES (?, 'guest', ?, ?)", (name, now, now)
        ).lastrowid
        token = secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO sessions(token_hash, user_id, created_at, last_seen) VALUES (?,?,?,?)",
            (hash_token(token), uid, now, now),
        )
        return Identity(uid, name, False, "guest", session_hash=hash_token(token)), token

    def rename(self, ident: Identity, name: str) -> None:
        if ident.kind == "proxy":
            raise ValueError("err.name_from_proxy")
        self.db.execute("UPDATE users SET name=? WHERE id=?", (clean_name(name), ident.user_id))

    def elevate(self, ident: Identity) -> None:
        if ident.session_hash:
            self.db.execute("UPDATE sessions SET is_admin=1 WHERE token_hash=?", (ident.session_hash,))

    def drop_admin(self, ident: Identity) -> None:
        if ident.session_hash:
            self.db.execute("UPDATE sessions SET is_admin=0 WHERE token_hash=?", (ident.session_hash,))

    def logout(self, ident: Identity) -> None:
        if ident.session_hash:
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (ident.session_hash,))

    def _proxy_identity(self, headers, state: dict) -> Identity | None:
        cfg = self.config
        if not cfg.auth_header or not state.get("via_trusted_proxy"):
            return None
        external = (headers.get(cfg.auth_header) or "").strip()
        if not external:
            return None
        external = external[:128]
        display = (headers.get(cfg.auth_name_header) if cfg.auth_name_header else None) or external
        display = " ".join(display.split())[:32] or external[:32]
        groups: set[str] = set()
        if cfg.groups_header:
            raw = headers.get(cfg.groups_header) or ""
            groups = {g.strip().lower() for g in re.split(r"[,|;]", raw) if g.strip()}
        is_admin = external.lower() in cfg.admin_users or bool(groups & set(cfg.admin_groups))
        row = self.db.one("SELECT * FROM users WHERE external_id=?", (external,))
        now = time.time()
        if row is None:
            uid = self.db.execute(
                "INSERT INTO users(name, kind, external_id, created_at, last_seen) VALUES (?, 'proxy', ?, ?, ?)",
                (display, external, now, now),
            ).lastrowid
            row = {"id": uid, "name": display, "role": "user", "banned": 0}
        elif row["name"] != display:
            self.db.execute("UPDATE users SET name=? WHERE id=?", (display, row["id"]))
        self._touch(row["id"])
        return Identity(
            row["id"], display, is_admin or row["role"] == "admin", "proxy",
            banned=bool(row["banned"]), can_logout=False,
        )

    def resolve(self, headers, cookies: dict, state: dict) -> Identity | None:
        ident = self._proxy_identity(headers, state)
        if ident:
            return ident

        authz = headers.get("authorization") or ""
        if authz.lower().startswith("bearer "):
            token = authz[7:].strip()
            row = self.db.one(
                "SELECT t.id AS tid, t.is_admin AS tadmin, u.* FROM api_tokens t JOIN users u ON u.id=t.user_id "
                "WHERE t.token_hash=?",
                (hash_token(token),),
            )
            if row:
                self.db.execute("UPDATE api_tokens SET last_used=? WHERE id=?", (time.time(), row["tid"]))
                return Identity(
                    row["id"], row["name"], bool(row["tadmin"]) or row["role"] == "admin", "api",
                    banned=bool(row["banned"]), can_logout=False,
                )
            return None

        token = cookies.get(COOKIE)
        if token:
            th = hash_token(token)
            row = self.db.one(
                "SELECT s.is_admin AS sadmin, s.last_seen AS slast, u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                "WHERE s.token_hash=?",
                (th,),
            )
            if row:
                now = time.time()
                if now - row["slast"] > 60:
                    self.db.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, th))
                    self._touch(row["id"])
                return Identity(
                    row["id"], row["name"], bool(row["sadmin"]) or row["role"] == "admin", row["kind"],
                    banned=bool(row["banned"]), session_hash=th,
                )
        return None

    # --- API tokens ----------------------------------------------------------
    def create_api_token(self, name: str, is_admin: bool) -> str:
        name = clean_name(name)
        now = time.time()
        uid = self.db.execute(
            "INSERT INTO users(name, kind, created_at, last_seen) VALUES (?, 'api', ?, ?)", (name, now, now)
        ).lastrowid
        token = "jbx_" + secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO api_tokens(name, token_hash, user_id, is_admin, created_at) VALUES (?,?,?,?,?)",
            (name, hash_token(token), uid, int(is_admin), now),
        )
        return token
