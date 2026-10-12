"""WebSocket fan-out. Each client gets its own bounded send queue so one
slow phone on bad Wi-Fi can never delay anybody else (or the player)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Callable

from starlette.websockets import WebSocket

from .auth import Identity
from .logs import get_logger

log = get_logger("hub")


@dataclass(eq=False)
class Client:
    ws: WebSocket
    ident: Identity | None
    display: bool = False
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=64))


class Hub:
    def __init__(self) -> None:
        self.clients: set[Client] = set()
        self.on_listeners_change: Callable[[], None] = lambda: None
        self._state_builder: Callable[[], dict] | None = None
        self._dirty = False
        self._flush_task: asyncio.Task | None = None

    def listener_ids(self) -> set[int]:
        return {c.ident.user_id for c in self.clients if c.ident and not c.display}

    def listeners(self) -> list[dict]:
        seen: dict[int, dict] = {}
        for c in self.clients:
            if c.ident and not c.display:
                seen[c.ident.user_id] = {"id": c.ident.user_id, "name": c.ident.name}
        return sorted(seen.values(), key=lambda u: u["name"].lower())

    async def serve(self, client: Client, initial: dict) -> None:
        before = self.listener_ids()
        self.clients.add(client)
        if self.listener_ids() != before:
            self.on_listeners_change()
        sender = asyncio.create_task(self._sender(client))
        try:
            await client.ws.send_text(json.dumps(initial, ensure_ascii=False))
            while True:  # we only need pings from clients; commands go over HTTP
                msg = await client.ws.receive_text()
                if msg == "ping":
                    self._put(client, '{"type":"pong"}')
        except Exception:
            pass
        finally:
            sender.cancel()
            before = self.listener_ids()
            self.clients.discard(client)
            if self.listener_ids() != before:
                self.on_listeners_change()

    async def _sender(self, client: Client) -> None:
        try:
            while True:
                text = await client.queue.get()
                await client.ws.send_text(text)
        except Exception:
            try:
                await client.ws.close()
            except Exception:
                pass

    def _put(self, client: Client, text: str) -> None:
        try:
            client.queue.put_nowait(text)
        except asyncio.QueueFull:  # hopelessly behind: drop it, it will reconnect and resync
            asyncio.create_task(client.ws.close(code=1013))

    def broadcast(self, message: dict, admins_only: bool = False) -> None:
        text = json.dumps(message, ensure_ascii=False)
        for c in list(self.clients):
            if admins_only and not (c.ident and c.ident.is_admin):
                continue
            self._put(c, text)

    def send_to_user(self, user_id: int, message: dict) -> None:
        text = json.dumps(message, ensure_ascii=False)
        for c in list(self.clients):
            if c.ident and c.ident.user_id == user_id:
                self._put(c, text)

    # --- coalesced state pushes -------------------------------------------
    def set_state_builder(self, fn: Callable[[], dict]) -> None:
        self._state_builder = fn

    def state_changed(self) -> None:
        """Mark state dirty; at most one push every 100 ms."""
        self._dirty = True
        if self._flush_task is None or self._flush_task.done():
            try:
                self._flush_task = asyncio.get_running_loop().create_task(self._flush())
            except RuntimeError:
                pass

    async def _flush(self) -> None:
        await asyncio.sleep(0.1)
        while self._dirty:
            self._dirty = False
            if self._state_builder and self.clients:
                try:
                    self.broadcast({"type": "state", "state": self._state_builder()})
                except Exception:
                    log.exception("state build failed")
            await asyncio.sleep(0.1)
