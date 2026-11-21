"""Register the slash commands with Discord only when they actually changed.

Syncing is a set of API calls that Discord rate limits, and with two hosts taking turns the bot
restarts often. A fingerprint of the command definitions is kept in the database, so a start
with unchanged commands makes no sync call at all. The back office button 同步指令 always syncs.
"""
import hashlib
import json
import logging

import discord
from discord.ext import commands

import config
import database

log = logging.getLogger(__name__)

FINGERPRINT_KEY = "commands_fingerprint"


def fingerprint(bot: commands.Bot) -> str:
    """Hash of everything Discord would be told: global commands and the admin server's commands."""
    tree = bot.tree
    payload = [command.to_dict(tree) for command in tree.get_commands()]
    if config.ADMIN_GUILD_ID:
        payload.append("admin-guild")
        payload += [command.to_dict(tree) for command in tree.get_commands(guild=discord.Object(id=config.ADMIN_GUILD_ID))]
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def sync(bot: commands.Bot, *, force: bool = False) -> tuple[int, bool]:
    """Sync if needed. Returns (number of commands Discord now has, whether a sync was made)."""
    current = fingerprint(bot)
    if not force:
        try:
            stored = await database.get_asset(FINGERPRINT_KEY)
        except database.DatabaseError:
            stored = None
        if stored is not None and stored.decode("utf-8", "ignore") == current:
            return 0, False

    count = len(await bot.tree.sync())
    if config.ADMIN_GUILD_ID:
        count += len(await bot.tree.sync(guild=discord.Object(id=config.ADMIN_GUILD_ID)))
    try:
        await database.set_asset(FINGERPRINT_KEY, current.encode("utf-8"))
    except database.DatabaseError:
        log.warning("Could not save the command fingerprint; the next start will sync again")
    return count, True
