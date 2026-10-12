"""yt-dlp wrapper.

yt-dlp runs as a low-priority subprocess (``nice``), never inside the web
process, so a hung or crashing extractor cannot hurt playback.  Because
YouTube & co. break extractors regularly, an admin (or a daily task) can
install a newer yt-dlp into the data volume (``/data/ytdlp``) which then
takes precedence over the version baked into the image.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

from .logs import get_logger

log = get_logger("ytdlp")


class YtdlpError(Exception):
    pass


def _lower_priority() -> None:  # runs in the child before exec
    try:
        os.nice(10)
    except OSError:
        pass


async def run_lowprio(args: list[str], timeout: float, env: dict | None = None) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        preexec_fn=_lower_priority,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        raise
    return proc.returncode or 0, out, err


def _friendly(stderr: str) -> str:
    s = stderr.lower()
    if "sign in to confirm" in s or "not a bot" in s:
        return "The source asks to confirm you're not a bot (try updating yt-dlp or adding cookies)."
    if "video unavailable" in s or "private video" in s:
        return "This video is unavailable or private."
    if "age" in s and "restricted" in s or "confirm your age" in s:
        return "Age-restricted content (needs cookies from a signed-in account)."
    if "http error 429" in s or "too many requests" in s:
        return "Rate-limited by the source. Try again later."
    if "http error 403" in s or "forbidden" in s:
        return "The source refused the request (HTTP 403). Updating yt-dlp usually fixes this."
    if "unable to download" in s or "timed out" in s or "name resolution" in s:
        return "Network problem reaching the source."
    if "drm" in s:
        return "This content is DRM-protected and can't be played."
    lines = [l for l in stderr.strip().splitlines() if "ERROR" in l]
    return (lines[-1] if lines else stderr.strip()[-300:]) or "Extractor failed."


class Ytdlp:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "ytdlp"
        self.current = self.root / "current"
        self.cookie_dir = Path(data_dir) / "cookies"
        self.last_update: float | None = None
        self.last_update_result: str | None = None
        self._version: str | None = None
        self._update_lock = asyncio.Lock()

    def env(self) -> dict:
        env = dict(os.environ)
        if (self.current / "yt_dlp").is_dir():
            env["PYTHONPATH"] = str(self.current) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return env

    def base_cmd(self, cookies: str | None = None) -> list[str]:
        cmd = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-warnings", "--no-progress",
               "--socket-timeout", "15", "--retries", "3"]
        if cookies:
            cmd += ["--cookies", cookies]
        return cmd

    def cookie_file(self, name: str, content: str | None) -> str | None:
        """Materialize a Netscape cookies.txt from provider config (0600)."""
        if not content or not content.strip():
            return None
        self.cookie_dir.mkdir(parents=True, exist_ok=True)
        path = self.cookie_dir / f"{name}.txt"
        if not path.exists() or path.read_text() != content:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(content)
        return str(path)

    async def json(self, target: str, *, flat: bool = True, timeout: float = 25.0,
                   cookies: str | None = None, extra: list[str] | None = None) -> dict:
        cmd = self.base_cmd(cookies) + ["-J", "--skip-download"]
        if flat:
            cmd.append("--flat-playlist")
        cmd += (extra or []) + ["--", target]
        try:
            code, out, err = await run_lowprio(cmd, timeout, self.env())
        except asyncio.TimeoutError:
            raise YtdlpError("The source took too long to answer.")
        try:
            data = json.loads(out) if out.strip() else None
        except ValueError:
            data = None
        if code != 0:
            # e.g. the 2nd page of search results got a 403: keep what we already have
            if isinstance(data, dict) and data.get("entries"):
                log.info("yt-dlp partial result for %s: %s", target, _friendly(err.decode(errors="replace")))
                return data
            raise YtdlpError(_friendly(err.decode(errors="replace")))
        if data is None:
            raise YtdlpError("Unexpected answer from yt-dlp.")
        return data

    async def download(self, url: str, out_base: Path, *, timeout: float = 180.0,
                       cookies: str | None = None, max_mb: int = 300) -> Path:
        """Download best audio without re-encoding; returns the final path."""
        out_base.parent.mkdir(parents=True, exist_ok=True)
        tmpl = str(out_base) + ".%(ext)s"
        cmd = self.base_cmd(cookies) + [
            "-f", "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
            "--no-playlist", "--no-part", "--no-mtime", "--max-filesize", f"{max_mb}M",
            "-o", tmpl, "--print", "after_move:filepath", "--", url,
        ]
        try:
            code, out, err = await run_lowprio(cmd, timeout, self.env())
        except asyncio.TimeoutError:
            raise YtdlpError("Download took too long.")
        if code != 0:
            raise YtdlpError(_friendly(err.decode(errors="replace")))
        lines = [l for l in out.decode(errors="replace").splitlines() if l.strip()]
        path = Path(lines[-1]) if lines else None
        if not path or not path.exists():
            matches = list(out_base.parent.glob(out_base.name + ".*"))
            if not matches:
                raise YtdlpError("Download finished but no file was produced.")
            path = matches[0]
        return path

    async def version(self) -> str:
        if self._version:
            return self._version
        try:
            code, out, _ = await run_lowprio([sys.executable, "-m", "yt_dlp", "--version"], 20, self.env())
            self._version = out.decode().strip() if code == 0 else "unknown"
        except Exception:
            self._version = "unknown"
        return self._version

    async def update(self) -> str:
        """Install the latest yt-dlp (+ its JS challenge solver) into the volume."""
        async with self._update_lock:
            new = self.root / "new"
            if new.exists():
                shutil.rmtree(new)
            new.mkdir(parents=True)
            cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
                   "--quiet", "--upgrade", "--target", str(new), "yt-dlp[default]"]
            try:
                code, _, err = await run_lowprio(cmd, 300)
            except asyncio.TimeoutError:
                code, err = 1, b"timeout"
            self.last_update = time.time()
            if code != 0:
                shutil.rmtree(new, ignore_errors=True)
                self.last_update_result = "failed: " + err.decode(errors="replace")[-300:]
                log.error("yt-dlp update failed: %s", self.last_update_result)
                return self.last_update_result
            old = self.root / "old"
            if old.exists():
                shutil.rmtree(old)
            if self.current.exists():
                self.current.rename(old)
            new.rename(self.current)
            shutil.rmtree(old, ignore_errors=True)
            self._version = None
            ver = await self.version()
            self.last_update_result = f"ok: {ver}"
            log.info("yt-dlp updated to %s", ver)
            return self.last_update_result

    async def rollback(self) -> None:
        """Go back to the version bundled in the image."""
        if self.current.exists():
            shutil.rmtree(self.current)
        self._version = None
