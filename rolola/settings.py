"""Runtime settings, editable from the admin panel and stored in SQLite.

Precedence: value saved from the admin UI > ``ROLOLA_DEFAULT_<KEY>``
environment variable > built-in default.  Secret values (provider
credentials) are encrypted at rest with a key from ``SECRET_KEY`` or a
generated ``secret.key`` file in the data volume.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken

from .db import Database, dumps, loads
from .logs import register_secret

DEFAULTS: dict[str, Any] = {
    "language": "en",
    "fairness_enabled": True,
    "max_queued_per_user": 10,
    "max_track_minutes": 15,
    "skip_mode": "percent",          # percent | count
    "skip_value": 50,
    "owner_can_skip": True,
    "volume": 50,
    "max_volume": 85,
    "volume_cooldown_s": 10,
    "user_can_pause": True,
    "user_can_seek": False,
    "user_can_volume": True,
    "autoplay_mode": "similar",      # off | playlist | local_shuffle | radio | history | similar
    "autoplay_playlist_id": None,
    "autoplay_station": None,        # Track dict of a radio station
    "autoplay_yield": True,          # user tracks interrupt autoplay
    "schedule_enabled": False,
    "schedule": [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "18:00"}],
    "normalize": True,
    "target_lufs": -18.0,
    "stream_gain_db": -4.0,
    "crossfade_s": 0,
    "allow_private_urls": False,
    "public_display": True,
    "substitute_unavailable": True,
    "audio_device": None,            # None -> AUDIO_DEVICE env
    "prefetch_count": 2,
    "stale_minutes": 60,             # paused/stopped this long -> drop the paused song and old queue
    "sponsorblock": True,
    "sponsorblock_categories": ["music_offtopic"],
    "lyrics": True,
}

# keys a normal user may read (sent with /api/state)
PUBLIC_KEYS = (
    "language", "fairness_enabled", "max_queued_per_user", "skip_mode", "skip_value",
    "owner_can_skip", "max_volume", "volume_cooldown_s", "user_can_pause", "user_can_seek",
    "user_can_volume", "schedule_enabled", "schedule", "autoplay_mode", "max_track_minutes", "lyrics",
)


def _coerce(default: Any, raw: str) -> Any:
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, (list, dict)) or default is None:
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


class Secrets:
    def __init__(self, data_dir: Path, secret_key: str):
        if secret_key:
            key = base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())
        else:
            path = data_dir / "secret.key"
            if path.exists():
                key = path.read_bytes().strip()
            else:
                key = Fernet.generate_key()
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(key)
        self.fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return "enc:" + self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        if not value.startswith("enc:"):
            return value
        try:
            return self.fernet.decrypt(value[4:].encode()).decode()
        except InvalidToken:
            return ""


class Settings:
    def __init__(self, db: Database, env_defaults: dict[str, str], secrets: Secrets):
        self.db = db
        self.secrets = secrets
        self.listeners: list[Callable[[str, Any], None]] = []
        self._env: dict[str, Any] = {}
        for key, raw in env_defaults.items():
            if key in DEFAULTS:
                try:
                    self._env[key] = _coerce(DEFAULTS[key], raw)
                except ValueError:
                    pass
        self._cache: dict[str, Any] = {r["key"]: loads(r["value"]) for r in db.all("SELECT * FROM settings")}

    def get(self, key: str) -> Any:
        if key in self._cache:
            return self._cache[key]
        if key in self._env:
            return self._env[key]
        return DEFAULTS.get(key)

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def set(self, key: str, value: Any) -> None:
        self._cache[key] = value
        self.db.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, dumps(value)),
        )
        for cb in self.listeners:
            cb(key, value)

    def reset(self, key: str) -> None:
        self._cache.pop(key, None)
        self.db.execute("DELETE FROM settings WHERE key=?", (key,))
        for cb in self.listeners:
            cb(key, self.get(key))

    def source(self, key: str) -> str:
        if key in self._cache:
            return "admin"
        if key in self._env:
            return "env"
        return "default"

    def all_public(self) -> dict[str, Any]:
        return {k: self.get(k) for k in PUBLIC_KEYS}

    def all_admin(self) -> dict[str, Any]:
        return {k: {"value": self.get(k), "source": self.source(k)} for k in DEFAULTS}

    # --- provider configuration (may contain secrets) -------------------
    def provider_config(self, provider_id: str) -> dict[str, Any]:
        raw = self._cache.get(f"provider.{provider_id}") or {}
        out = {}
        for k, v in raw.items():
            if isinstance(v, str) and v.startswith("enc:"):
                v = self.secrets.decrypt(v)
                register_secret(v)
            out[k] = v
        return out

    def set_provider_config(self, provider_id: str, values: dict[str, Any], secret_fields: set[str]) -> None:
        current = dict(self._cache.get(f"provider.{provider_id}") or {})
        for k, v in values.items():
            if k in secret_fields:
                if v in (None, "", "***"):  # unchanged / keep existing
                    if v == "":
                        current.pop(k, None)
                    continue
                register_secret(str(v))
                current[k] = self.secrets.encrypt(str(v))
            else:
                current[k] = v
        self.set(f"provider.{provider_id}", current)
