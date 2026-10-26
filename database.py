"""Per-server settings storage.

Two interchangeable backends, chosen by environment variables:
- Turso (cloud SQLite) over its HTTP API, when TURSO_DATABASE_URL and
  TURSO_AUTH_TOKEN are set. Uses aiohttp, which discord.py already installs.
- A local SQLite file (data/bot.db) otherwise.

Design notes for a 512 MB / 0.1 CPU host:
- No background tasks. Network calls only happen when a setting is read for the
  first time or changed by an admin.
- Settings are cached in memory (a few hundred bytes per server), so member
  joins and leaves never touch the database after the first read.
- Local SQLite connections are short-lived and closed immediately.
"""
import asyncio
import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import aiohttp

import config

log = logging.getLogger(__name__)

Statement = tuple[str, tuple]


class DatabaseError(Exception):
    """Raised for any storage failure, whichever backend is in use."""


SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id   INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS welcome_settings (
        guild_id        INTEGER PRIMARY KEY REFERENCES guild_settings(guild_id) ON DELETE CASCADE,
        enabled         INTEGER NOT NULL DEFAULT 0,
        channel_id      INTEGER,
        message         TEXT    NOT NULL,
        use_embed       INTEGER NOT NULL DEFAULT 1,
        image_enabled   INTEGER NOT NULL DEFAULT 0,
        background_path TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS leave_settings (
        guild_id   INTEGER PRIMARY KEY REFERENCES guild_settings(guild_id) ON DELETE CASCADE,
        enabled    INTEGER NOT NULL DEFAULT 0,
        channel_id INTEGER,
        message    TEXT    NOT NULL,
        use_embed  INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS autorole_settings (
        guild_id INTEGER PRIMARY KEY REFERENCES guild_settings(guild_id) ON DELETE CASCADE,
        enabled  INTEGER NOT NULL DEFAULT 0,
        role_id  INTEGER
    )""",
]

# Maps the short kind name used by the cogs to its table and editable columns.
_TABLES = {
    "welcome": ("welcome_settings", {"enabled", "channel_id", "message", "use_embed",
                                     "image_enabled", "background_path"}),
    "leave": ("leave_settings", {"enabled", "channel_id", "message", "use_embed"}),
    "autorole": ("autorole_settings", {"enabled", "role_id"}),
}


# ------------------------------------------------------------------ backends
@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Open a local connection, commit on success, roll back on error, always close."""
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


class _LocalBackend:
    name = "local SQLite"

    async def run(self, statements: list[Statement]) -> list[list[dict]]:
        """Run statements in one transaction; return the rows of each statement."""
        try:
            with _connect() as conn:
                results = []
                for sql, args in statements:
                    cursor = conn.execute(sql, args)
                    results.append([dict(row) for row in cursor.fetchall()] if cursor.description else [])
                return results
        except sqlite3.Error as exc:
            raise DatabaseError(str(exc)) from exc

    async def close(self) -> None:
        return None


def _encode(value: Any) -> dict:
    """Convert a Python value to a Hrana typed value."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, (bool, int)):
        return {"type": "integer", "value": str(int(value))}
    return {"type": "text", "value": str(value)}


def _decode(cell: dict) -> Any:
    """Convert a Hrana typed value back to a Python value."""
    kind = cell.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        return int(cell["value"])
    if kind == "float":
        return float(cell["value"])
    return cell.get("value")


class _TursoBackend:
    """Minimal Turso client using the HTTP pipeline API (one request per operation)."""
    name = "Turso"

    def __init__(self, url: str, token: str) -> None:
        base = url.strip().replace("libsql://", "https://", 1).rstrip("/")
        self._endpoint = f"{base}/v2/pipeline"
        self._token = token
        self._session: Optional[aiohttp.ClientSession] = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def _post(self, body: dict) -> dict:
        last_error: Optional[Exception] = None
        for attempt in range(2):  # one quick retry for transient network problems
            try:
                async with self._get_session().post(self._endpoint, json=body) as response:
                    if response.status != 200:
                        detail = (await response.text())[:200]
                        raise DatabaseError(f"Turso returned HTTP {response.status}: {detail}")
                    return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                await asyncio.sleep(0.5)
        raise DatabaseError(f"Could not reach Turso: {last_error}") from last_error

    async def run(self, statements: list[Statement]) -> list[list[dict]]:
        requests = [{"type": "execute", "stmt": {"sql": sql, "args": [_encode(a) for a in args]}}
                    for sql, args in statements]
        requests.append({"type": "close"})
        data = await self._post({"requests": requests})

        results: list[list[dict]] = []
        for item in data.get("results", [])[:len(statements)]:
            if item.get("type") != "ok":
                message = (item.get("error") or {}).get("message", "unknown error")
                raise DatabaseError(f"Turso rejected a statement: {message}")
            result = item["response"]["result"]
            names = [column["name"] for column in result.get("cols", [])]
            results.append([dict(zip(names, (_decode(cell) for cell in row)))
                            for row in result.get("rows", [])])
        if len(results) != len(statements):
            raise DatabaseError("Turso returned an incomplete response")
        return results

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


# ------------------------------------------------------------------- state
_backend: Optional[Any] = None
_cache: dict[tuple[str, int], dict] = {}
_CACHE_LIMIT = 5000  # far above any realistic server count; just a safety net


async def init_db() -> None:
    """Pick the backend and create all tables. Safe to call on every start."""
    global _backend
    if config.TURSO_DATABASE_URL and config.TURSO_AUTH_TOKEN:
        _backend = _TursoBackend(config.TURSO_DATABASE_URL, config.TURSO_AUTH_TOKEN)
    else:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _backend = _LocalBackend()
    config.CUSTOM_BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    await _backend.run([(sql, ()) for sql in SCHEMA_STATEMENTS])
    log.info("Database ready (%s)", _backend.name)


async def close() -> None:
    if _backend is not None:
        await _backend.close()


def _default_statements(kind: str, guild_id: int) -> list[Statement]:
    statements: list[Statement] = [("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))]
    if kind == "welcome":
        statements.append(("INSERT OR IGNORE INTO welcome_settings (guild_id, message) VALUES (?, ?)",
                           (guild_id, config.DEFAULT_WELCOME_MESSAGE)))
    elif kind == "leave":
        statements.append(("INSERT OR IGNORE INTO leave_settings (guild_id, message) VALUES (?, ?)",
                           (guild_id, config.DEFAULT_LEAVE_MESSAGE)))
    else:
        statements.append(("INSERT OR IGNORE INTO autorole_settings (guild_id) VALUES (?)", (guild_id,)))
    return statements


async def get_settings(kind: str, guild_id: int) -> dict[str, Any]:
    """Return one server's settings as a dict, creating default values on first use."""
    key = (kind, guild_id)
    cached = _cache.get(key)
    if cached is not None:
        return dict(cached)

    table, _ = _TABLES[kind]
    select = (f"SELECT * FROM {table} WHERE guild_id = ?", (guild_id,))
    rows = (await _backend.run([select]))[0]
    if not rows:
        rows = (await _backend.run(_default_statements(kind, guild_id) + [select]))[-1]
    if not rows:
        raise DatabaseError("Could not create default settings")

    if len(_cache) >= _CACHE_LIMIT:
        _cache.clear()
    _cache[key] = rows[0]
    return dict(rows[0])


async def update_settings(kind: str, guild_id: int, **fields: Any) -> None:
    """Update one or more columns for a server. Unknown column names are rejected."""
    table, allowed = _TABLES[kind]
    unknown = set(fields) - allowed
    if unknown or not fields:
        raise ValueError(f"Invalid columns for {table}: {sorted(unknown)}")
    assignments = ", ".join(f"{column} = ?" for column in fields)
    statements = _default_statements(kind, guild_id)
    statements.append((f"UPDATE {table} SET {assignments} WHERE guild_id = ?", (*fields.values(), guild_id)))
    await _backend.run(statements)

    cached = _cache.get((kind, guild_id))  # keep the cache in step, only after a successful write
    if cached is not None:
        cached.update(fields)
