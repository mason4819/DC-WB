"""Leave system: on_raw_member_remove listener and /leave commands."""
import logging
import sqlite3
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import database
from utils.formatter import VARIABLES_HELP, clean_template, render_message
from utils.messaging import STYLE_CHOICES, AdminGroup, channel_warnings, deliver, reply

log = logging.getLogger(__name__)

MESSAGE_HELP = f"Message text. Variables: {VARIABLES_HELP}"
MessageText = app_commands.Range[str, 1, config.MAX_MESSAGE_LENGTH]


class LeaveCog(commands.Cog, name="Leave"):
    leave = AdminGroup(name="leave", description="Configure the leave system")

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
            settings = database.get_settings("leave", guild.id)
            if not settings["enabled"] or not settings["channel_id"]:
                return
            sent, notes = await self._send_leave(guild, payload.user, settings)
            if not sent or notes:
                log.warning("Leave message in guild %s: sent=%s notes=%s", guild.id, sent, notes)
        except sqlite3.Error:
            log.exception("Database error while handling a leave in guild %s", guild.id)
        except Exception:
            log.exception("Unexpected error while handling a leave in guild %s", guild.id)

    # ----------------------------------------------------------------- helpers
    async def _send_leave(self, guild: discord.Guild, user: discord.abc.User,
                          settings: dict) -> tuple[bool, list[str]]:
        text = render_message(settings["message"], user, guild)
        return await deliver(
            bot=self.bot, guild=guild, channel_id=settings["channel_id"], user=user, text=text,
            use_embed=bool(settings["use_embed"]), colour=config.LEAVE_COLOR, ping_user=False,
        )

    # --------------------------------------------------------------- commands
    @leave.command(name="setup", description="Turn on the leave system and choose a channel")
    @app_commands.describe(channel="Channel where leave messages are posted", message=MESSAGE_HELP,
                           style="Plain text or embed")
    @app_commands.choices(style=STYLE_CHOICES)
    async def leave_setup(self, interaction: discord.Interaction, channel: discord.TextChannel,
                          message: Optional[MessageText] = None,
                          style: Optional[app_commands.Choice[str]] = None) -> None:
        fields: dict = {"enabled": 1, "channel_id": channel.id}
        if message:
            fields["message"] = clean_template(message)
        if style:
            fields["use_embed"] = int(style.value == "embed")
        database.update_settings("leave", interaction.guild_id, **fields)

        settings = database.get_settings("leave", interaction.guild_id)
        notes = await channel_warnings(self.bot, channel, embeds=bool(settings["use_embed"]), files=False)
        text = f"✅ Leave messages are now enabled in {channel.mention}."
        if notes:
            text += "\n" + "\n".join(f"⚠️ {note}" for note in notes)
        text += "\nTry it with `/leave test`."
        await reply(interaction, text)

    @leave.command(name="enable", description="Enable the leave system")
    async def leave_enable(self, interaction: discord.Interaction) -> None:
        settings = database.get_settings("leave", interaction.guild_id)
        database.update_settings("leave", interaction.guild_id, enabled=1)
        if not settings["channel_id"]:
            await reply(interaction, "✅ Enabled, but no channel is set yet. Use `/leave channel` to pick one.")
        else:
            await reply(interaction, "✅ The leave system is enabled.")

    @leave.command(name="disable", description="Disable the leave system")
    async def leave_disable(self, interaction: discord.Interaction) -> None:
        database.update_settings("leave", interaction.guild_id, enabled=0)
        await reply(interaction, "✅ The leave system is disabled. Your settings are kept.")

    @leave.command(name="channel", description="Choose the leave channel")
    @app_commands.describe(channel="Channel where leave messages are posted")
    async def leave_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        database.update_settings("leave", interaction.guild_id, channel_id=channel.id)
        settings = database.get_settings("leave", interaction.guild_id)
        notes = await channel_warnings(self.bot, channel, embeds=bool(settings["use_embed"]), files=False)
        text = f"✅ Leave messages will be posted in {channel.mention}."
        if notes:
            text += "\n" + "\n".join(f"⚠️ {note}" for note in notes)
        await reply(interaction, text)

    @leave.command(name="message", description="Set the leave message text and style")
    @app_commands.describe(text=MESSAGE_HELP, style="Plain text or embed")
    @app_commands.choices(style=STYLE_CHOICES)
    async def leave_message(self, interaction: discord.Interaction, text: Optional[MessageText] = None,
                            style: Optional[app_commands.Choice[str]] = None) -> None:
        if text is None and style is None:
            settings = database.get_settings("leave", interaction.guild_id)
            kind = "embed" if settings["use_embed"] else "plain text"
            await reply(interaction, f"Current style: **{kind}**\nCurrent message:\n```{settings['message']}```\n"
                                     f"Variables: `{VARIABLES_HELP}`\nUse `\\n` for a line break.")
            return
        fields: dict = {}
        if text is not None:
            fields["message"] = clean_template(text)
        if style is not None:
            fields["use_embed"] = int(style.value == "embed")
        database.update_settings("leave", interaction.guild_id, **fields)
        await reply(interaction, "✅ Leave message updated. Check it with `/leave test`.")

    @leave.command(name="test", description="Send a test leave message to the leave channel")
    async def leave_test(self, interaction: discord.Interaction) -> None:
        settings = database.get_settings("leave", interaction.guild_id)
        if not settings["channel_id"]:
            await reply(interaction, "❌ No leave channel is set. Use `/leave channel` first.")
            return
        await interaction.response.defer(ephemeral=True)
        sent, notes = await self._send_leave(interaction.guild, interaction.user, settings)
        text = f"✅ Test message sent to <#{settings['channel_id']}>." if sent \
            else "❌ The test message could not be sent."
        if notes:
            text += "\n" + "\n".join(f"⚠️ {note}" for note in notes)
        await reply(interaction, text)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LeaveCog(bot))
