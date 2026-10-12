"""ASGI middleware for running behind a reverse proxy.

* Strips ``BASE_PATH`` (when the proxy forwards ``/jukebox/...`` unchanged)
  while also serving the root path (when the proxy strips the prefix).
* Honors ``X-Forwarded-For/Proto/Host/Prefix`` only from ``TRUSTED_PROXIES``.
* Records the *direct* peer address so identity headers are only trusted
  when they come from the proxy itself, never from a LAN client.
"""

from __future__ import annotations

from .config import Config


class ProxyMiddleware:
    def __init__(self, app, config: Config):
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        scope = dict(scope)
        peer = scope.get("client")[0] if scope.get("client") else None
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        trusted = self.config.is_trusted_proxy(peer)
        state = dict(scope.get("state") or {})
        state["direct_peer"] = peer
        state["via_trusted_proxy"] = trusted
        prefix = self.config.base_path

        if trusted:
            xff = headers.get("x-forwarded-for")
            if xff:
                # Walk from the right, skipping our own trusted proxies.
                hops = [h.strip() for h in xff.split(",") if h.strip()]
                client_ip = hops[0] if hops else peer
                for hop in reversed(hops):
                    if not self.config.is_trusted_proxy(hop):
                        client_ip = hop
                        break
                scope["client"] = (client_ip, 0)
            proto = headers.get("x-forwarded-proto")
            if proto:
                proto = proto.split(",")[0].strip().lower()
                if scope["type"] == "websocket":
                    scope["scheme"] = "wss" if proto in ("https", "wss") else "ws"
                else:
                    scope["scheme"] = "https" if proto in ("https", "wss") else "http"
            fwd_prefix = headers.get("x-forwarded-prefix")
            if fwd_prefix:
                prefix = "/" + fwd_prefix.strip().strip("/") if fwd_prefix.strip("/") else ""
            host = headers.get("x-forwarded-host")
            if host:
                new_headers = [(k, v) for k, v in scope["headers"] if k.lower() != b"host"]
                new_headers.append((b"host", host.split(",")[0].strip().encode("latin-1")))
                scope["headers"] = new_headers

        path: str = scope.get("path", "")
        base = self.config.base_path
        if base and path == base and scope["type"] == "http":
            # "/jukebox" -> "/jukebox/" so relative URLs in the UI resolve correctly
            await send({"type": "http.response.start", "status": 308,
                        "headers": [(b"location", (base + "/").encode()), (b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return
        if base and (path == base or path.startswith(base + "/")):
            scope["path"] = path[len(base):] or "/"
            raw = scope.get("raw_path")
            if raw and raw.startswith(base.encode()):
                scope["raw_path"] = raw[len(base):] or b"/"
            prefix = base
        state["public_prefix"] = prefix
        scope["state"] = state
        return await self.app(scope, receive, send)
