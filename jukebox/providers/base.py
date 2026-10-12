"""The provider (music source) plugin interface.

A provider is a class with an ``id``, some metadata, and async methods.  To
add a source, subclass :class:`Provider`, implement ``search`` and
``resolve`` (plus optionally ``expand``/``similar``/``random_tracks``), and
register it in ``providers/__init__.py``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from ..models import Playable, Track

if TYPE_CHECKING:
    from ..services import Services

Status = Literal["connected", "needs_config", "disabled", "broken", "rate_limited", "offline", "planned"]


class ProviderError(Exception):
    """A plain-language message safe to show to users."""

    def __init__(self, message: str, status: Status | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class ConfigField:
    key: str
    label: str
    type: Literal["text", "secret", "bool", "number", "textarea", "select"] = "text"
    help: str = ""
    default: Any = None
    options: list[str] = field(default_factory=list)

    def public(self) -> dict:
        return {"key": self.key, "label": self.label, "type": self.type, "help": self.help,
                "default": self.default, "options": self.options}


class Provider:
    id: str = "base"
    name: str = "Base"
    description: str = ""
    # honest notes shown in the admin panel
    account: str = ""
    legal: str = ""
    risk: Literal["low", "medium", "high"] = "low"
    needs_internet: bool = True
    default_enabled: bool = False
    capabilities: tuple[str, ...] = ("search",)
    config_fields: list[ConfigField] = []

    def __init__(self, services: "Services"):
        self.svc = services
        self.status: Status = "connected"
        self.status_detail: str = ""
        self.last_error_at: float | None = None
        self.last_ok_at: float | None = None

    # --- configuration ------------------------------------------------------
    @property
    def config(self) -> dict[str, Any]:
        cfg = {f.key: f.default for f in self.config_fields}
        cfg.update(self.svc.settings.provider_config(self.id))
        return cfg

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", self.default_enabled))

    def configured(self) -> bool:
        """Override when credentials are required."""
        return True

    def secret_fields(self) -> set[str]:
        return {f.key for f in self.config_fields if f.type == "secret" or "cookie" in f.key}

    # --- status helpers -----------------------------------------------------
    def ok(self) -> None:
        self.status = "connected"
        self.status_detail = ""
        self.last_ok_at = time.time()

    def fail(self, exc: Exception) -> None:
        self.last_error_at = time.time()
        status = getattr(exc, "status", None)
        msg = str(exc)
        if status is None:
            low = msg.lower()
            if "429" in low or "rate" in low:
                status = "rate_limited"
            elif "network" in low or "resolve" in low or "connect" in low:
                status = "offline"
            else:
                status = "broken"
        self.status = status
        self.status_detail = msg[:300]

    def current_status(self) -> tuple[Status, str]:
        if not self.enabled:
            return "disabled", ""
        if not self.configured():
            return "needs_config", self.status_detail or "Credentials required."
        return self.status, self.status_detail

    def info(self, admin: bool = False) -> dict:
        status, detail = self.current_status()
        d = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "status": status,
            "detail": detail,
            "capabilities": list(self.capabilities),
        }
        if admin:
            cfg = self.config
            secrets = self.secret_fields()
            d.update({
                "account": self.account,
                "legal": self.legal,
                "risk": self.risk,
                "needs_internet": self.needs_internet,
                "fields": [f.public() for f in self.config_fields],
                "config": {k: ("***" if k in secrets and v else v) for k, v in cfg.items()},
                "last_ok_at": self.last_ok_at,
                "last_error_at": self.last_error_at,
            })
        return d

    # --- the actual interface -----------------------------------------------
    async def start(self) -> None:
        """Called once at startup (and after config changes)."""

    async def search(self, query: str, limit: int = 20) -> list[Track]:
        raise NotImplementedError

    async def expand(self, item: Track) -> list[Track]:
        """Turn an album/playlist/podcast into tracks."""
        return [item]

    async def resolve(self, track: Track) -> Playable:
        """Return what mpv should open (may download to the cache)."""
        raise NotImplementedError

    async def similar(self, track: Track, limit: int = 10) -> list[Track]:
        return []

    async def random_tracks(self, limit: int = 10) -> list[Track]:
        return []

    async def test(self) -> str:
        """Connectivity test for the admin panel."""
        await self.search("test", limit=1)
        return "OK"

    def owns_url(self, url: str) -> bool:
        return False
