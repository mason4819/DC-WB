"""Image-only worker (optional). Runs just the web server from webapp.py.

Use this on a host that should ONLY draw images for the bot and never connect to
Discord. For two-host failover, run bot.py on both hosts instead.

Environment:
  IMAGE_WORKER_SECRET  required, same value as on the bot
  PORT / SERVER_PORT / WORKER_PORT  port to listen on (default 8080)

Run with:  python worker.py
"""
import logging
import os
import sys

from aiohttp import web

import config  # loads .env
import webapp


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                        stream=sys.stdout)
    if not config.IMAGE_WORKER_SECRET:
        sys.exit("IMAGE_WORKER_SECRET is missing. Set the same value on the bot and on the worker.")
    port = config.PORT or int(os.getenv("WORKER_PORT") or 8080)
    logging.getLogger("worker").info("Image worker listening on port %d", port)
    web.run_app(webapp.create_app(lambda: "worker"), host="0.0.0.0", port=port, access_log=None, print=None)


if __name__ == "__main__":
    main()
