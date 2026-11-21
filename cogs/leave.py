"""Leave system: the on_raw_member_remove listener. Configured in the /設定 panel (panels/leave.py)."""
import logging
import discord
from discord.ext import commands

import config
import database
from utils.formatter import render_message
from utils.messaging import deliver

log = logging.getLogger(__name__)

class LeaveCog(commands.Cog, name="Leave"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ events
    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent) -> None:
        # The raw event is used because the member cache is disabled to save RAM,
        # and on_member_remove only fires for cached members.
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        try:
            settings = await database.get_settings("leave", guild.id)
            if not settings["enabled"] or not settings["channel_id"]:
                return
            sent, notes = await self.send_leave(guild, payload.user, settings)
            if not sent or notes:
                log.warning("Leave message in guild %s: sent=%s notes=%s", guild.id, sent, notes)
        except database.DatabaseError:
            log.exception("Database error while handling a leave in guild %s", guild.id)
        except Exception:
            log.exception("Unexpected error while handling a leave in guild %s", guild.id)

    # ----------------------------------------------------------------- helpers
    async def send_leave(self, guild: discord.Guild, user: discord.abc.User,
                          settings: dict) -> tuple[bool, list[str]]:
        text = render_message(settings["message"], user, guild)
        return await deliver(
            bot=self.bot, guild=guild, channel_id=settings["channel_id"], user=user, text=text,
            use_embed=bool(settings["use_embed"]), colour=config.LEAVE_COLOR, ping_user=False,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LeaveCog(bot))
