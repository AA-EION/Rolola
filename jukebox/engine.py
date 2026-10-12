"""mpv audio engine, driven over its JSON IPC socket.

mpv runs as a supervised child process.  It writes straight to ALSA
(``--ao=alsa --audio-device=alsa/<AUDIO_DEVICE>``), keeps a 1 s output
buffer, plays the preloaded next file gaplessly and applies per-file gain.
If mpv dies it is restarted with back-off and the player resumes.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any, Awaitable, Callable

from .logs import get_logger

log = get_logger("engine")

EventHandler = Callable[[dict], Awaitable[None]]


class EngineError(Exception):
    pass


def mpv_audio_args(device: str, disable_audio: bool) -> list[str]:
    device = (device or "").strip()
    if disable_audio or device in ("null", "none"):
        return ["--ao=null"]
    if device in ("", "auto"):
        return ["--ao=alsa"]
    if not device.startswith("alsa/"):
        device = "alsa/" + device
    return ["--ao=alsa", f"--audio-device={device}"]


class MpvEngine:
    OBSERVED = ("pause", "volume", "idle-active", "duration", "metadata", "current-ao", "seeking")

    def __init__(
        self,
        socket_path: Path,
        binary: str = "mpv",
        extra_args: list[str] | None = None,
        on_event: EventHandler | None = None,
        on_restart: Callable[[], Awaitable[None]] | None = None,
    ):
        self.socket_path = Path(socket_path)
        self.binary = binary
        self.extra_args = extra_args or []
        self.on_event = on_event
        self.on_restart = on_restart
        self.device = "default"
        self.disable_audio = False
        self.initial_volume = 50.0
        self.proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        self.ready = asyncio.Event()
        self.props: dict[str, Any] = {}
        self.restarts = 0
        self.last_error: str | None = None
        self.stderr_tail: list[str] = []

    # --- lifecycle --------------------------------------------------------
    def args(self) -> list[str]:
        return [
            self.binary,
            "--idle=yes",
            "--no-config",
            "--no-video",
            "--vid=no",
            "--no-terminal",
            "--audio-display=no",
            "--load-scripts=no",
            "--ytdl=no",
            "--input-default-bindings=no",
            f"--input-ipc-server={self.socket_path}",
            *mpv_audio_args(self.device, self.disable_audio),
            "--gapless-audio=weak",
            "--prefetch-playlist=yes",
            "--audio-buffer=1.0",
            "--cache=yes",
            "--demuxer-max-bytes=64MiB",
            "--demuxer-readahead-secs=20",
            "--network-timeout=15",
            "--replaygain=track",
            "--replaygain-clip=no",
            "--volume-max=100",
            f"--volume={self.initial_volume:.0f}",
            "--msg-level=all=warn",
            *self.extra_args,
        ]

    async def start(self) -> None:
        self._stopping = False
        self._tasks.append(asyncio.create_task(self._supervise(), name="mpv-supervisor"))

    async def stop(self) -> None:
        self._stopping = True
        for t in self._tasks:
            t.cancel()
        await self._kill()

    async def restart(self) -> None:
        """Restart mpv (e.g. after changing the audio device)."""
        await self._kill()

    async def _kill(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.send_signal(signal.SIGTERM)
                await asyncio.wait_for(self.proc.wait(), 3)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass

    async def _supervise(self) -> None:
        backoff = 1.0
        first = True
        while not self._stopping:
            try:
                await self._spawn()
                backoff = 1.0
                if not first and self.on_restart:
                    await self.on_restart()
                first = False
                await self.proc.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # spawn/connect failure
                self.last_error = str(exc)
                log.error("mpv failed to start: %s", exc)
            finally:
                self.ready.clear()
                self._fail_pending("mpv exited")
            if self._stopping:
                break
            self.restarts += 1
            code = self.proc.returncode if self.proc else None
            log.warning("mpv exited (code %s); restarting in %.0fs", code, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _spawn(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        self.proc = await asyncio.create_subprocess_exec(
            *self.args(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._tasks.append(asyncio.create_task(self._read_stderr(self.proc), name="mpv-stderr"))
        for _ in range(100):
            if self.socket_path.exists() or self.proc.returncode is not None:
                break
            await asyncio.sleep(0.05)
        if self.proc.returncode is not None:
            raise EngineError(f"mpv exited immediately (code {self.proc.returncode})")
        self._reader, self._writer = await asyncio.open_unix_connection(str(self.socket_path))
        self._tasks.append(asyncio.create_task(self._read_ipc(self._reader), name="mpv-ipc"))
        for n, prop in enumerate(self.OBSERVED, start=1):
            await self.command("observe_property", n, prop)
        self.ready.set()
        self.last_error = None
        log.info("mpv started", extra={"ctx": {"device": self.device, "pid": self.proc.pid}})

    async def _read_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode(errors="replace").rstrip()
            if text:
                self.stderr_tail = (self.stderr_tail + [text])[-50:]
                log.warning("mpv: %s", text)

    async def _read_ipc(self, reader: asyncio.StreamReader) -> None:
        while True:
            line = await reader.readline()
            if not line:
                return
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "request_id" in msg and "event" not in msg:
                fut = self._pending.pop(msg["request_id"], None)
                if fut and not fut.done():
                    if msg.get("error") == "success":
                        fut.set_result(msg.get("data"))
                    else:
                        fut.set_exception(EngineError(msg.get("error", "error")))
                continue
            if msg.get("event") == "property-change":
                self.props[msg.get("name")] = msg.get("data")
            if self.on_event:
                try:
                    await self.on_event(msg)
                except Exception:
                    log.exception("error handling mpv event %s", msg.get("event"))

    def _fail_pending(self, reason: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(EngineError(reason))
        self._pending.clear()

    # --- commands ---------------------------------------------------------
    async def command(self, *args: Any, timeout: float = 5.0) -> Any:
        if not self._writer:
            raise EngineError("mpv not connected")
        self._next_id += 1
        rid = self._next_id
        cmd: Any = args[0] if len(args) == 1 and isinstance(args[0], dict) else list(args)
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        self._writer.write((json.dumps({"command": cmd, "request_id": rid}) + "\n").encode())
        await self._writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    async def get(self, prop: str, default: Any = None) -> Any:
        try:
            return await self.command("get_property", prop)
        except EngineError:
            return default

    async def set(self, prop: str, value: Any) -> None:
        await self.command("set_property", prop, value)

    async def loadfile(self, target: str, mode: str = "replace", options: dict[str, str] | None = None) -> int | None:
        cmd: dict[str, Any] = {"name": "loadfile", "url": target, "flags": mode}
        if options:
            cmd["options"] = options
        res = await self.command(cmd)
        return (res or {}).get("playlist_entry_id")

    @property
    def alive(self) -> bool:
        return self.ready.is_set() and self.proc is not None and self.proc.returncode is None


def alsa_cards() -> list[dict]:
    """Parse /proc/asound/cards for diagnostics."""
    cards = []
    path = Path("/proc/asound/cards")
    if not path.exists():
        return cards
    lines = path.read_text(errors="replace").splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if line and line[0].isdigit() and "[" in line:
            num = line.split()[0]
            ident = line[line.index("[") + 1 : line.index("]")].strip()
            desc = line.split(":", 1)[1].strip() if ":" in line else ""
            long = lines[i + 1].strip() if i + 1 < len(lines) else ""
            cards.append({"index": int(num), "id": ident, "name": desc, "long_name": long,
                          "suggested_device": f"plughw:CARD={ident},DEV=0"})
    return cards


def snd_access() -> dict:
    dev = Path("/dev/snd")
    if not dev.exists():
        return {"present": False, "writable": False, "nodes": []}
    nodes = sorted(p.name for p in dev.iterdir())
    pcm = [dev / n for n in nodes if n.startswith("pcm") and n.endswith("p")]
    writable = any(os.access(p, os.R_OK | os.W_OK) for p in pcm) if pcm else False
    return {"present": True, "writable": writable, "nodes": nodes, "groups": os.getgroups()}
