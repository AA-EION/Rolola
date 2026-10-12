"""Structured logging: JSON lines on stdout plus an in-memory ring buffer
that the admin panel can display. Known secret values are redacted."""

from __future__ import annotations

import collections
import json
import logging
import sys
import time

_SECRETS: set[str] = set()
RING: collections.deque[dict] = collections.deque(maxlen=1000)


def register_secret(value: str | None) -> None:
    """Never let this string appear in a log line."""
    if value and len(value) >= 4:
        _SECRETS.add(value)


def _redact(text: str) -> str:
    for s in _SECRETS:
        if s in text:
            text = text.replace(s, "***")
    return text


class _Formatter(logging.Formatter):
    def __init__(self, as_json: bool):
        super().__init__()
        self.as_json = as_json

    def format(self, record: logging.LogRecord) -> str:
        msg = _redact(record.getMessage())
        entry = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": msg,
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            entry.update({k: _redact(str(v)) for k, v in extra.items()})
        if record.exc_info:
            entry["exc"] = _redact(self.formatException(record.exc_info))
        RING.append(entry)
        if self.as_json:
            return json.dumps(entry, ensure_ascii=False)
        t = time.strftime("%H:%M:%S", time.localtime(record.created))
        tail = "".join(f" {k}={v}" for k, v in entry.items() if k not in ("ts", "level", "logger", "msg"))
        return f"{t} {record.levelname:<7} {record.name}: {msg}{tail}"


def setup_logging(level: str = "INFO", as_json: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Formatter(as_json))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn's access log is noisy and would log query strings; keep errors only.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"rolola.{name}")


def log(logger: logging.Logger, level: int, msg: str, **ctx) -> None:
    logger.log(level, msg, extra={"ctx": ctx})
