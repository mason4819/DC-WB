"""HTTP health-check and authenticated admin dashboard server.

Uses aiohttp, which discord.py already depends on, so nothing extra is installed.
"""
import logging
from typing import Optional

from aiohttp import web

import config
import web_dashboard

log = logging.getLogger(__name__)


async def _ok(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def start(port: int, bot) -> Optional[web.AppRunner]:
    """Start the server if a port is configured; otherwise do nothing."""
    if not port:
        return None
    if not config.WEB_USERNAME or not config.WEB_PASSWORD.strip():
        raise RuntimeError("Set both WEB_USERNAME and WEB_PASSWORD before starting the web server.")
    app = web.Application(client_max_size=16 * 1024)
    app.add_routes([web.get("/health", _ok)])  # GET routes also answer HEAD
    web_dashboard.register_routes(app, bot)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Web dashboard and health check listening on port %d", port)
    return runner


async def stop(runner: Optional[web.AppRunner]) -> None:
    if runner is not None:
        await runner.cleanup()
