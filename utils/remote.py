"""Client for the optional image worker (worker.py running on another host).

If IMAGE_WORKER_URL / IMAGE_WORKER_SECRET are not set, or the worker does not
answer, render() returns None and the bot draws the image itself.
Requests are signed with HMAC-SHA256 so only this bot can use the worker.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import aiohttp

import config

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10
COOLDOWN_SECONDS = 60          # after a failure, skip the worker for a minute

_session: Optional[aiohttp.ClientSession] = None
_down_until = 0.0


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """Signature shared by the bot and the worker."""
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def is_enabled() -> bool:
    return bool(config.IMAGE_WORKER_URL and config.IMAGE_WORKER_SECRET)


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS))
    return _session


async def render(avatar: Optional[bytes], display_name: str, server_name: str, member_count: int,
                 background: Optional[bytes], alt_name: str) -> Optional[bytes]:
    """Ask the worker for a welcome image. Returns PNG bytes, or None to fall back to local rendering."""
    global _down_until
    if not is_enabled() or time.monotonic() < _down_until:
        return None

    body = json.dumps({
        "avatar": base64.b64encode(avatar).decode() if avatar else None,
        "background": base64.b64encode(background).decode() if background else None,
        "display_name": display_name,
        "server_name": server_name,
        "member_count": member_count,
        "alt_name": alt_name,
    }).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Timestamp": timestamp,
        "X-Signature": sign(config.IMAGE_WORKER_SECRET, timestamp, body),
    }
    try:
        async with _get_session().post(f"{config.IMAGE_WORKER_URL}/render", data=body, headers=headers) as response:
            if response.status != 200:
                raise RuntimeError(f"worker answered HTTP {response.status}")
            png = await response.read()
        if not png.startswith(b"\x89PNG"):
            raise RuntimeError("worker did not return a PNG")
        return png
    except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as exc:
        _down_until = time.monotonic() + COOLDOWN_SECONDS
        log.warning("Image worker unavailable (%s). Rendering locally for the next %d s.", exc, COOLDOWN_SECONDS)
        return None


async def close() -> None:
    if _session is not None and not _session.closed:
        await _session.close()


async def ping_peer() -> bool:
    """GET /health on the other host. Also wakes it up if it is a sleeping free-tier service."""
    if not config.PEER_URL:
        return False
    try:
        async with _get_session().get(f"{config.PEER_URL}/health", timeout=aiohttp.ClientTimeout(total=30)) as response:
            return response.status == 200
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return False


async def check_peer() -> Optional[dict]:
    """Ask the other host who it is. None = unreachable; a dict (maybe empty) = reachable.

    The dict holds "node" (its NODE_NAME) and "role" ("leader" / "standby") when available.
    """
    if not config.PEER_URL:
        return None
    try:
        async with _get_session().get(f"{config.PEER_URL}/status", timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status == 200:
                data = await response.json(content_type=None)
                return data if isinstance(data, dict) else {}
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None
    return {} if await ping_peer() else None   # an older version without /status: /health still works
