"""Exceptions shared across modules (kept import-free to avoid cycles)."""

from __future__ import annotations


class ProviderError(Exception):
    """A plain-language message safe to show to users."""

    def __init__(self, message: str, status: str | None = None):
        super().__init__(message)
        self.status = status
