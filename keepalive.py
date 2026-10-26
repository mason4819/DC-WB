"""Tiny health-check web server for hosts that require an open port (Render).

Uses aiohttp, which discord.py already depends on, so nothing extra is installed.
It answers GET/HEAD on "/" and "/health" with a few bytes and does nothing else.
Point UptimeRobot at the public URL to keep a free web service awake.
"""
import logging
from typing import Optional

from aiohttp import web

log = logging.getLogger(__name__)


async def _ok(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def start(port: int) -> Optional[web.AppRunner]:
    """Start the server if a port is configured; otherwise do nothing."""
    if not port:
        return None
    app = web.Application()
    app.add_routes([web.get("/", _ok), web.get("/health", _ok)])  # GET routes also answer HEAD
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Health check listening on port %d", port)
    return runner


async def stop(runner: Optional[web.AppRunner]) -> None:
    if runner is not None:
        await runner.cleanup()
