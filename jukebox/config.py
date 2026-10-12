"""Static configuration read once from environment variables.

Everything here is *deployment* configuration (paths, ports, proxy trust).
Runtime behaviour (fairness, volume limits, sources...) lives in
``settings.py`` and can be edited from the admin panel; environment variables
with the ``JUKEBOX_DEFAULT_`` prefix only seed those runtime settings.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _csv(name: str) -> list[str]:
    return [p.strip() for p in _env(name).split(",") if p.strip()]


def _normalize_base_path(raw: str) -> str:
    raw = raw.strip()
    if not raw or raw == "/":
        return ""
    return "/" + raw.strip("/")


@dataclass
class Config:
    data_dir: Path
    music_dir: Path
    host: str
    port: int
    base_path: str
    audio_device: str
    mpv_binary: str
    mpv_extra_args: list[str]
    trusted_proxies: list[ipaddress._BaseNetwork]
    auth_header: str
    auth_name_header: str
    groups_header: str
    admin_users: list[str]
    admin_groups: list[str]
    admin_password: str
    secret_key: str
    log_level: str
    log_json: bool
    cache_max_mb: int
    ytdlp_auto_update: bool
    library_scan_interval_min: int
    disable_audio: bool
    defaults: dict[str, str] = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jukebox.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def run_dir(self) -> Path:
        return self.data_dir / "run"

    def is_trusted_proxy(self, ip: str | None) -> bool:
        if not ip:
            return False
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in self.trusted_proxies)


def load_config() -> Config:
    proxies: list[ipaddress._BaseNetwork] = []
    for item in _csv("TRUSTED_PROXIES"):
        try:
            proxies.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            pass

    defaults = {
        k[len("JUKEBOX_DEFAULT_"):].lower(): v
        for k, v in os.environ.items()
        if k.startswith("JUKEBOX_DEFAULT_")
    }

    return Config(
        data_dir=Path(_env("DATA_DIR", "/data")),
        music_dir=Path(_env("MUSIC_DIR", "/music")),
        host=_env("HOST", "0.0.0.0"),
        port=_env_int("PORT", 8080),
        base_path=_normalize_base_path(_env("BASE_PATH")),
        audio_device=_env("AUDIO_DEVICE", "default"),
        mpv_binary=_env("MPV_BINARY", "mpv"),
        mpv_extra_args=_env("MPV_EXTRA_ARGS").split(),
        trusted_proxies=proxies,
        auth_header=_env("AUTH_HEADER"),
        auth_name_header=_env("AUTH_NAME_HEADER"),
        groups_header=_env("AUTH_GROUPS_HEADER"),
        admin_users=[u.lower() for u in _csv("ADMIN_USERS")],
        admin_groups=[g.lower() for g in _csv("ADMIN_GROUPS")],
        admin_password=os.environ.get("ADMIN_PASSWORD", ""),
        secret_key=os.environ.get("SECRET_KEY", ""),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
        log_json=_env_bool("LOG_JSON", True),
        cache_max_mb=_env_int("CACHE_MAX_MB", 2048),
        ytdlp_auto_update=_env_bool("YTDLP_AUTO_UPDATE", True),
        library_scan_interval_min=_env_int("LIBRARY_SCAN_INTERVAL_MIN", 60),
        disable_audio=_env_bool("DISABLE_AUDIO", False),
        defaults=defaults,
    )
