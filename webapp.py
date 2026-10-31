"""Small web server that every host runs.

  GET  /, /health  answers "OK" (Render's port check, UptimeRobot, the other host's heartbeat)
  GET  /status     shows this host's name and whether it is the active bot or on standby
  POST /render     draws a welcome image for the other host (signed with IMAGE_WORKER_SECRET)
"""
import asyncio
import base64
import binascii
import hmac
import json
import logging
import time
from typing import Callable, Optional

from aiohttp import web

import busywork
import config
import ha
from utils.image import RENDER_LOCK, build_welcome_image
from utils.remote import sign

log = logging.getLogger(__name__)

MAX_REQUEST_BYTES = 3 * 1024 * 1024   # avatar + background + text
MAX_AGE_SECONDS = 60                  # reject old (replayed) requests


def _text(data: dict, key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError(key)
    return value


def _optional_bytes(data: dict, key: str) -> Optional[bytes]:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(key)
    return base64.b64decode(value, validate=True)


async def _ok(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def _render(request: web.Request) -> web.Response:
    secret = config.IMAGE_WORKER_SECRET
    if not secret:
        return web.Response(status=503, text="image worker is not configured")
    body = await request.read()
    timestamp = request.headers.get("X-Timestamp", "")
    signature = request.headers.get("X-Signature", "")
    try:
        fresh = abs(time.time() - float(timestamp)) <= MAX_AGE_SECONDS
    except ValueError:
        fresh = False
    if not fresh or not hmac.compare_digest(signature, sign(secret, timestamp, body)):
        return web.Response(status=401, text="unauthorized")

    try:
        data = json.loads(body)
        avatar = _optional_bytes(data, "avatar")
        background = _optional_bytes(data, "background")
        display_name = _text(data, "display_name")
        server_name = _text(data, "server_name")
        alt_name = _text(data, "alt_name")
        member_count = int(data["member_count"])
        if not 0 <= member_count <= 10**9:
            raise ValueError("member_count")
    except (ValueError, KeyError, TypeError, binascii.Error):
        return web.Response(status=400, text="bad request")

    try:
        async with RENDER_LOCK:
            with busywork.busy():
                image = await asyncio.to_thread(build_welcome_image, avatar, display_name, server_name,
                                                member_count, background, alt_name)
        return web.Response(body=image.getvalue(), content_type="image/png")
    except Exception:
        log.exception("Rendering a request from the other host failed")
        return web.Response(status=500, text="render failed")


def create_app(role: Callable[[], str]) -> web.Application:
    async def status(request: web.Request) -> web.Response:
        return web.json_response({"node": config.NODE_NAME, "role": role(), "peer": dict(ha.peer)})

    app = web.Application(client_max_size=MAX_REQUEST_BYTES)
    app.add_routes([web.get("/", _ok), web.get("/health", _ok), web.get("/status", status),
                    web.post("/render", _render)])
    return app


async def start(port: int, role: Callable[[], str]) -> Optional[web.AppRunner]:
    """Start the server if this host has a port; otherwise do nothing."""
    if not port:
        log.info("No PORT found: the web server is off (failover still works through the database)")
        return None
    runner = web.AppRunner(create_app(role), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Web server listening on port %d", port)
    return runner


async def stop(runner: Optional[web.AppRunner]) -> None:
    if runner is not None:
        await runner.cleanup()
