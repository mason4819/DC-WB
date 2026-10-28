"""Optional image worker. Deploy this on a second host (e.g. Wispbyte).

It does one job: receive a signed request from the bot, draw the welcome image
with Pillow and send the PNG back. It never talks to Discord and stores nothing.

Environment:
  IMAGE_WORKER_SECRET  required, must be identical on the bot and the worker
  PORT / SERVER_PORT / WORKER_PORT  port to listen on (first one found, default 8080)

Run with:  python worker.py
"""
import asyncio
import base64
import binascii
import hmac
import json
import logging
import os
import sys
import time

from aiohttp import web

import config  # loads .env
from utils.image import build_welcome_image
from utils.remote import sign

log = logging.getLogger("worker")

MAX_REQUEST_BYTES = 3 * 1024 * 1024   # avatar + background + text
MAX_AGE_SECONDS = 60                  # reject old (replayed) requests
SECRET = os.getenv("IMAGE_WORKER_SECRET", "").strip()
_render_lock = asyncio.Semaphore(1)   # one image at a time keeps RAM and CPU low


def _text(data: dict, key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError(key)
    return value


def _optional_bytes(data: dict, key: str) -> "bytes | None":
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(key)
    return base64.b64decode(value, validate=True)


async def health(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def render(request: web.Request) -> web.Response:
    body = await request.read()
    timestamp = request.headers.get("X-Timestamp", "")
    signature = request.headers.get("X-Signature", "")
    try:
        fresh = abs(time.time() - float(timestamp)) <= MAX_AGE_SECONDS
    except ValueError:
        fresh = False
    if not fresh or not hmac.compare_digest(signature, sign(SECRET, timestamp, body)):
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
        async with _render_lock:
            image = await asyncio.to_thread(build_welcome_image, avatar, display_name, server_name,
                                            member_count, background, alt_name)
        return web.Response(body=image.getvalue(), content_type="image/png")
    except Exception:
        log.exception("Rendering failed")
        return web.Response(status=500, text="render failed")


def create_app() -> web.Application:
    app = web.Application(client_max_size=MAX_REQUEST_BYTES)
    app.add_routes([web.get("/", health), web.get("/health", health), web.post("/render", render)])
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                        stream=sys.stdout)
    if not SECRET:
        sys.exit("IMAGE_WORKER_SECRET is missing. Set the same value on the bot and on the worker.")
    port = int(os.getenv("PORT") or os.getenv("SERVER_PORT") or os.getenv("WORKER_PORT") or 8080)
    log.info("Image worker listening on port %d", port)
    web.run_app(create_app(), host="0.0.0.0", port=port, access_log=None, print=None)


if __name__ == "__main__":
    main()
