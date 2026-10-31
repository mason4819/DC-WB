"""Central configuration: paths, limits and default texts.

Nothing here is a per-server setting. Per-server settings live in SQLite.
"""
import os
import re
import socket
import time
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

STARTED_AT = time.time()  # roughly when this process started (for the uptime display)

# --- Environment -----------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
# Set SYNC_COMMANDS=false after the first successful start to skip re-syncing.
SYNC_COMMANDS = os.getenv("SYNC_COMMANDS", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
# Render (and similar hosts) set PORT for web services. When present, a tiny health
# check server starts so uptime monitors can ping the bot. 0 = no web server.
def _first_port() -> int:
    for key in ("PORT", "SERVER_PORT", "WORKER_PORT"):
        value = os.getenv(key, "").strip()
        if value.isdigit():
            return int(value)
    return 0


PORT = _first_port()

# Optional external database (Turso). When both are set, settings are stored there
# instead of the local SQLite file, so they survive redeploys on hosts with a
# temporary disk. Accepts libsql:// or https:// URLs.
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()

# Optional: offload welcome image rendering to a second host running worker.py
# (for example on Wispbyte). If the worker is unreachable the bot renders locally.
IMAGE_WORKER_URL = os.getenv("IMAGE_WORKER_URL", "").strip().rstrip("/")
IMAGE_WORKER_SECRET = os.getenv("IMAGE_WORKER_SECRET", "").strip()

def _int_list(name: str) -> list[int]:
    return [int(part) for part in re.split(r"[,\s]+", os.getenv(name, "")) if part.isdigit()]


# Back office (/後臺): Discord user IDs allowed to use it. Leave empty to use the bot's
# application owner (or team) from the Developer Portal. Set on every host.
OWNER_IDS = _int_list("OWNER_IDS")
# Optional: show the /後臺 commands only in this server instead of in every server.
ADMIN_GUILD_ID = next(iter(_int_list("ADMIN_GUILD_ID")), 0)

# --- Two-host failover (Render <-> Wispbyte) --------------------------------
# Failover needs the shared Turso database, which also holds the "who is active" lease.
NODE_NAME = os.getenv("NODE_NAME", "").strip() or socket.gethostname()
# Public URL of the other host. Used for heartbeat pings (and image offloading if
# IMAGE_WORKER_URL is also set). Falls back to IMAGE_WORKER_URL.
PEER_URL = (os.getenv("PEER_URL", "").strip() or IMAGE_WORKER_URL).rstrip("/")
HA_ENABLED = bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)

# Optional low-priority busywork while the process has nothing to do (see busywork.py).
KEEP_BUSY = os.getenv("KEEP_BUSY", "true").strip().lower() in {"1", "true", "yes", "on"}
KEEP_BUSY_PERCENT = float(os.getenv("KEEP_BUSY_PERCENT", "1") or 1)

# --- Paths -----------------------------------------------------------------
# Set DATA_DIR to a persistent disk path on hosts that provide one (e.g. /var/data on Render).
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).expanduser()
DB_PATH = DATA_DIR / "bot.db"
CUSTOM_BACKGROUND_DIR = DATA_DIR / "backgrounds"  # per-server uploaded backgrounds
ASSETS_DIR = BASE_DIR / "assets"
FONT_DIR = ASSETS_DIR / "fonts"
DEFAULT_BACKGROUND = ASSETS_DIR / "backgrounds" / "default.png"

# --- Welcome image ---------------------------------------------------------
IMAGE_WIDTH = 1200
IMAGE_HEIGHT = 406                                      # same ratio as the default banner (500x169)
MAX_BACKGROUND_BYTES = 5 * 1024 * 1024                  # 5 MB upload limit
MAX_BACKGROUND_PIXELS = 12_000_000                      # protects RAM when decoding uploads

# --- Message defaults and limits -------------------------------------------
DEFAULT_WELCOME_MESSAGE = "歡迎 {user} 加入 {server}！你是第 {member_count} 位成員。"
DEFAULT_LEAVE_MESSAGE = "{username} 離開了 {server}，目前共有 {member_count} 位成員。"
MAX_MESSAGE_LENGTH = 1000

# --- Embed colours ---------------------------------------------------------
WELCOME_COLOR = 0xE53945
LEAVE_COLOR = 0x6B7280
WARNING_COLOR = 0xF59E0B
