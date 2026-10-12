"""In-memory token-bucket rate limiter (single process, so this is enough)."""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}
        self._last_gc = time.monotonic()

    def allow(self, scope: str, key: str, rate: float, per: float) -> bool:
        """Allow ``rate`` events per ``per`` seconds for (scope, key)."""
        now = time.monotonic()
        tokens, last = self._buckets.get((scope, key), (rate, now))
        tokens = min(rate, tokens + (now - last) * rate / per)
        ok = tokens >= 1.0
        if ok:
            tokens -= 1.0
        self._buckets[(scope, key)] = (tokens, now)
        if now - self._last_gc > 300:
            self._gc(now)
        return ok

    def _gc(self, now: float) -> None:
        self._last_gc = now
        stale = [k for k, (_, last) in self._buckets.items() if now - last > 3600]
        for k in stale:
            del self._buckets[k]
