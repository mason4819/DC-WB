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
    """CREATE TABLE IF NOT EXISTS node_lease (
        name       TEXT PRIMARY KEY,
        holder     TEXT NOT NULL,
        expires_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS access_list (
        guild_id  INTEGER NOT NULL REFERENCES guild_settings(guild_id) ON DELETE CASCADE,
        kind      TEXT    NOT NULL CHECK (kind IN ('user', 'role')),
        target_id INTEGER NOT NULL,
        PRIMARY KEY (guild_id, kind, target_id)
    )""",
    """CREATE TABLE IF NOT EXISTS blocked_guilds (
        guild_id   INTEGER PRIMARY KEY,
        name       TEXT NOT NULL,
        blocked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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


def backend_name() -> str:
    return _backend.name if _backend is not None else "not ready"


def cache_size() -> int:
    return len(_cache)


def clear_cache() -> None:
    """Forget every cached row. Called when this host becomes the active bot, because the
    other host may have changed settings while this one was on standby."""
    _cache.clear()


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


# ------------------------------------------------------------ failover lease
# One row says which host currently owns the Discord connection. Expiry is judged
# with the database's own clock, so the two hosts never have to agree on the time.
LEASE_NAME = "discord"
_DB_NOW = "CAST(strftime('%s','now') AS INTEGER)"


async def try_acquire_lease(holder: str, ttl: int) -> bool:
    """Take or renew the lease. True only if `holder` owns it afterwards (atomic)."""
    statement = (
        f"""INSERT INTO node_lease (name, holder, expires_at) VALUES (?, ?, {_DB_NOW} + ?)
            ON CONFLICT(name) DO UPDATE SET holder = excluded.holder, expires_at = excluded.expires_at
            WHERE node_lease.holder = excluded.holder OR node_lease.expires_at <= {_DB_NOW}
            RETURNING holder""",
        (LEASE_NAME, holder, ttl),
    )
    rows = (await _backend.run([statement]))[0]
    return bool(rows) and rows[0]["holder"] == holder


async def release_lease(holder: str) -> None:
    """Give the lease up immediately so the other host can take over without waiting."""
    await _backend.run([("UPDATE node_lease SET expires_at = 0 WHERE name = ? AND holder = ?",
                         (LEASE_NAME, holder))])


# ------------------------------------------------------- command access list
# Which members and roles may use the setup commands (besides the owner and Administrators).
MAX_ACCESS_ENTRIES = 25  # per kind (users / roles) per server


async def get_access(guild_id: int) -> dict[str, list[int]]:
    """Return {"user": [ids], "role": [ids]}. Read once per server, then served from memory."""
    key = ("access", guild_id)
    cached = _cache.get(key)
    if cached is None:
        rows = (await _backend.run([("SELECT kind, target_id FROM access_list WHERE guild_id = ?",
                                     (guild_id,))]))[0]
        cached = {"user": [r["target_id"] for r in rows if r["kind"] == "user"],
                  "role": [r["target_id"] for r in rows if r["kind"] == "role"]}
        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        _cache[key] = cached
    return {kind: list(ids) for kind, ids in cached.items()}


async def add_access(guild_id: int, kind: str, target_id: int) -> str:
    """Add a member or role. Returns "added", "exists" or "full"."""
    current = await get_access(guild_id)
    if target_id in current[kind]:
        return "exists"
    if len(current[kind]) >= MAX_ACCESS_ENTRIES:
        return "full"
    await _backend.run([
        ("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,)),
        ("INSERT OR IGNORE INTO access_list (guild_id, kind, target_id) VALUES (?, ?, ?)",
         (guild_id, kind, target_id)),
    ])
    _cache[("access", guild_id)] = {**current, kind: [*current[kind], target_id]}
    return "added"


async def remove_access(guild_id: int, kind: str, target_id: int) -> bool:
    """Remove a member or role. Returns False if it was not on the list."""
    current = await get_access(guild_id)
    if target_id not in current[kind]:
        return False
    await _backend.run([("DELETE FROM access_list WHERE guild_id = ? AND kind = ? AND target_id = ?",
                         (guild_id, kind, target_id))])
    _cache[("access", guild_id)] = {**current, kind: [i for i in current[kind] if i != target_id]}
    return True


async def clear_access(guild_id: int) -> None:
    """Empty the list, which returns the server to the default rule (Manage Server)."""
    await _backend.run([("DELETE FROM access_list WHERE guild_id = ?", (guild_id,))])
    _cache[("access", guild_id)] = {"user": [], "role": []}


# ------------------------------------------------------------ back office
MAX_BLOCKED = 500


async def peek_settings(kind: str, guild_id: int) -> Optional[dict[str, Any]]:
    """Read one server's settings WITHOUT creating default rows. None if it never configured them."""
    cached = _cache.get((kind, guild_id))
    if cached is not None:
        return dict(cached)
    table, _ = _TABLES[kind]
    rows = (await _backend.run([(f"SELECT * FROM {table} WHERE guild_id = ?", (guild_id,))]))[0]
    return rows[0] if rows else None


async def delete_guild_data(guild_id: int) -> None:
    """Remove everything stored for a server (settings, management list)."""
    tables = ("access_list", "welcome_settings", "leave_settings", "autorole_settings", "guild_settings")
    await _backend.run([(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,)) for table in tables])
    for key in [key for key in _cache if key[1] == guild_id]:
        _cache.pop(key, None)


async def get_blocked() -> dict[int, str]:
    """Servers the bot must leave on sight: {guild_id: name}."""
    cached = _cache.get(("blocked", 0))
    if cached is None:
        rows = (await _backend.run([("SELECT guild_id, name FROM blocked_guilds", ())]))[0]
        cached = {"items": {row["guild_id"]: row["name"] for row in rows}}
        _cache[("blocked", 0)] = cached
    return dict(cached["items"])


async def add_blocked(guild_id: int, name: str) -> str:
    """Returns "added", "exists" or "full"."""
    current = await get_blocked()
    if guild_id in current:
        return "exists"
    if len(current) >= MAX_BLOCKED:
        return "full"
    await _backend.run([("INSERT OR REPLACE INTO blocked_guilds (guild_id, name) VALUES (?, ?)", (guild_id, name))])
    _cache[("blocked", 0)] = {"items": {**current, guild_id: name}}
    return "added"


async def remove_blocked(guild_id: int) -> bool:
    current = await get_blocked()
    if guild_id not in current:
        return False
    await _backend.run([("DELETE FROM blocked_guilds WHERE guild_id = ?", (guild_id,))])
    current.pop(guild_id)
    _cache[("blocked", 0)] = {"items": current}
    return True


async def get_lease() -> Optional[dict[str, Any]]:
    """Who owns the failover lease and for how many more seconds (database clock)."""
    rows = (await _backend.run([(
        f"SELECT holder, expires_at - {_DB_NOW} AS seconds_left FROM node_lease WHERE name = ?",
        (LEASE_NAME,))]))[0]
    return rows[0] if rows else None
