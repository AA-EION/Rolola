"""Network helpers: URL safety checks (SSRF protection) and playlist parsing."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from .providers.base import ProviderError

USER_AGENT = "OfficeJukebox/1.0 (+https://github.com/aa-eion/rolola)"


async def check_public_url(url: str, allow_private: bool = False) -> str:
    """Reject non-http(s) URLs and, unless allowed, URLs pointing into the LAN
    or at the container itself (so users can't make the server probe your
    router's admin page)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        raise ProviderError("That doesn't look like a valid URL.")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ProviderError("Only http:// and https:// links are supported.")
    if allow_private:
        return url
    host = parsed.hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ProviderError("Couldn't resolve that address (are we offline?).", status="offline")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ProviderError("Links to local network addresses are blocked by the admin.")
    return url


def parse_playlist(body: str) -> str | None:
    """First stream URL from an .m3u/.pls body."""
    for line in body.splitlines():
        line = line.strip()
        if line.lower().startswith("file") and "=" in line:
            line = line.split("=", 1)[1].strip()
        if line.startswith(("http://", "https://")):
            return line
    return None


async def resolve_stream_url(client: httpx.AsyncClient, url: str) -> str:
    """Follow .pls/.m3u indirections so mpv gets a direct stream."""
    path = urlparse(url).path.lower()
    if not path.endswith((".pls", ".m3u", ".m3u8")) or path.endswith(".m3u8"):
        return url
    try:
        r = await client.get(url, timeout=10)
        r.raise_for_status()
        first = parse_playlist(r.text[:20000])
        return first or url
    except httpx.HTTPError:
        return url
