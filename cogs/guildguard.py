"""Keeps the bot out of servers on the block list (managed in the back office of the /設定 panel)."""
import logging

import discord
from discord.ext import commands

import database

log = logging.getLogger(__name__)


class GuildGuardCog(commands.Cog, name="GuildGuard"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        try:
            if guild.id in await database.get_blocked():
                log.warning("Leaving blocked guild %s (%s)", guild.name, guild.id)
                await guild.leave()
            else:
                log.info("Joined guild %s (%s), %s members", guild.name, guild.id, guild.member_count)
        except (database.DatabaseError, discord.HTTPException):
            log.exception("Could not process joining guild %s", guild.id)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Catch servers that invited the bot while it was offline (for example during a failover)."""
        try:
            blocked = await database.get_blocked()
            for guild in [g for g in self.bot.guilds if g.id in blocked]:
                log.warning("Leaving blocked guild %s (%s)", guild.name, guild.id)
                await guild.leave()
        except (database.DatabaseError, discord.HTTPException):
            log.exception("Blocked guild check failed")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildGuardCog(bot))
