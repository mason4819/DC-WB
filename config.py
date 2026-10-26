"""Central configuration: paths, limits and default texts.

Nothing here is a per-server setting. Per-server settings live in SQLite.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# --- Environment -----------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
# Set SYNC_COMMANDS=false after the first successful start to skip re-syncing.
SYNC_COMMANDS = os.getenv("SYNC_COMMANDS", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
# Render (and similar hosts) set PORT for web services. When present, a tiny health
# check server starts so uptime monitors can ping the bot. 0 = no web server.
PORT = int(os.getenv("PORT", "0") or 0)

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
IMAGE_HEIGHT = 500
MAX_BACKGROUND_BYTES = 5 * 1024 * 1024                  # 5 MB upload limit
MAX_BACKGROUND_PIXELS = 12_000_000                      # protects RAM when decoding uploads

# --- Message defaults and limits -------------------------------------------
DEFAULT_WELCOME_MESSAGE = "Welcome {user} to {server}! You are member #{member_count}."
DEFAULT_LEAVE_MESSAGE = "{username} has left {server}. We now have {member_count} members."
MAX_MESSAGE_LENGTH = 1000

# --- Embed colours ---------------------------------------------------------
WELCOME_COLOR = 0xE53945
LEAVE_COLOR = 0x6B7280
WARNING_COLOR = 0xF59E0B
