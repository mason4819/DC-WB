"""Images the bot needs at runtime. The database is the source of truth.

- Custom backgrounds are stored in the database; a copy is kept as a small file under data/
  (named with a version number) so a render does not need a database round trip.
  On a fresh host, after a redeploy or after a switch to the other host, the file is simply
  fetched from the database again.
- The "generating..." thumbnail: an owner-uploaded version in the database, otherwise the
  GIF that ships in assets/loading.gif.
"""
import logging
from typing import Optional

import discord

import config
import database

log = logging.getLogger(__name__)

LOADING_FILENAME = "loading.gif"
_bundled_loading: Optional[bytes] = None


def _cache_path(guild_id: int, version: int):
    return config.CUSTOM_BACKGROUND_DIR / f"{guild_id}_{version}.jpg"


def drop_background_cache(guild_id: int) -> None:
    """Remove every cached copy of a server's background (new upload, reset or wipe)."""
    for path in config.CUSTOM_BACKGROUND_DIR.glob(f"{guild_id}_*.jpg"):
        path.unlink(missing_ok=True)
    (config.CUSTOM_BACKGROUND_DIR / f"{guild_id}.jpg").unlink(missing_ok=True)    # old layout


def cache_background(guild_id: int, version: int, data: bytes) -> None:
    """Write the cache copy for this version and delete the older ones."""
    drop_background_cache(guild_id)
    config.CUSTOM_BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(guild_id, version)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


async def background_file(guild_id: int, version: int):
    """Path of the cached background for this version, fetched from the database if needed."""
    path = _cache_path(guild_id, version)
    if path.is_file():
        return path
    data = await database.load_background(guild_id)
    if data is None:
        return None
    try:
        cache_background(guild_id, version, data)
    except OSError:
        log.warning("Could not write the background cache for guild %s", guild_id)
        return None
    return path


async def loading_bytes() -> Optional[bytes]:
    """The loading thumbnail: owner upload from the database, else the bundled GIF."""
    global _bundled_loading
    try:
        custom = await database.get_asset("loading")
        if custom:
            return custom
    except database.DatabaseError:
        log.warning("Could not read the loading image from the database; using the bundled one")
    if _bundled_loading is None:
        try:
            _bundled_loading = config.LOADING_IMAGE.read_bytes()
        except OSError:
            log.warning("The bundled loading image is missing")
            return None
    return _bundled_loading


async def loading_file() -> Optional[discord.File]:
    import io
    data = await loading_bytes()
    return discord.File(io.BytesIO(data), filename=LOADING_FILENAME) if data else None
