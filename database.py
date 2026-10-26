"""SQLite storage for per-server settings.

Design notes for a 512 MB host:
- A short-lived connection is opened per operation and closed immediately.
  No connection pool, no background thread.
- Reads never write, except the very first read for a server, which inserts
  its default row once.
- Queries are tiny, so they run directly on the event loop.
"""
import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

import config

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id   INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS welcome_settings (
    guild_id        INTEGER PRIMARY KEY REFERENCES guild_settings(guild_id) ON DELETE CASCADE,
    enabled         INTEGER NOT NULL DEFAULT 0,
    channel_id      INTEGER,
    message         TEXT    NOT NULL,
    use_embed       INTEGER NOT NULL DEFAULT 1,
    image_enabled   INTEGER NOT NULL DEFAULT 0,
    background_path TEXT
);

CREATE TABLE IF NOT EXISTS leave_settings (
    guild_id   INTEGER PRIMARY KEY REFERENCES guild_settings(guild_id) ON DELETE CASCADE,
    enabled    INTEGER NOT NULL DEFAULT 0,
    channel_id INTEGER,
    message    TEXT    NOT NULL,
    use_embed  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS autorole_settings (
    guild_id INTEGER PRIMARY KEY REFERENCES guild_settings(guild_id) ON DELETE CASCADE,
    enabled  INTEGER NOT NULL DEFAULT 0,
    role_id  INTEGER
);
"""

# Maps the short kind name used by the cogs to its table and editable columns.
_TABLES = {
    "welcome": ("welcome_settings", {"enabled", "channel_id", "message", "use_embed",
                                     "image_enabled", "background_path"}),
    "leave": ("leave_settings", {"enabled", "channel_id", "message", "use_embed"}),
    "autorole": ("autorole_settings", {"enabled", "role_id"}),
}


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error, always close."""
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the data folders and all tables. Safe to call on every start."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.CUSTOM_BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA)
    log.info("Database ready at %s", config.DB_PATH)


def _insert_defaults(conn: sqlite3.Connection, kind: str, guild_id: int) -> None:
    conn.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
    if kind == "welcome":
        conn.execute("INSERT OR IGNORE INTO welcome_settings (guild_id, message) VALUES (?, ?)",
                     (guild_id, config.DEFAULT_WELCOME_MESSAGE))
    elif kind == "leave":
        conn.execute("INSERT OR IGNORE INTO leave_settings (guild_id, message) VALUES (?, ?)",
                     (guild_id, config.DEFAULT_LEAVE_MESSAGE))
    else:
        conn.execute("INSERT OR IGNORE INTO autorole_settings (guild_id) VALUES (?)", (guild_id,))


def get_settings(kind: str, guild_id: int) -> dict[str, Any]:
    """Return one server's settings as a dict, creating default values on first use."""
    table, _ = _TABLES[kind]
    with _connect() as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE guild_id = ?", (guild_id,)).fetchone()
        if row is None:
            _insert_defaults(conn, kind, guild_id)
            row = conn.execute(f"SELECT * FROM {table} WHERE guild_id = ?", (guild_id,)).fetchone()
        return dict(row)


def update_settings(kind: str, guild_id: int, **fields: Any) -> None:
    """Update one or more columns for a server. Unknown column names are rejected."""
    table, allowed = _TABLES[kind]
    unknown = set(fields) - allowed
    if unknown or not fields:
        raise ValueError(f"Invalid columns for {table}: {sorted(unknown)}")
    assignments = ", ".join(f"{column} = ?" for column in fields)
    with _connect() as conn:
        _insert_defaults(conn, kind, guild_id)
        conn.execute(f"UPDATE {table} SET {assignments} WHERE guild_id = ?",
                     (*fields.values(), guild_id))
