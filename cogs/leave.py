"""Leave system: on_raw_member_remove listener and the /離開 commands."""
import logging
import sqlite3
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import database
from utils.formatter import VARIABLES_DESCRIPTION, VARIABLES_HELP, clean_template, render_message
from utils.messaging import STYLE_CHOICES, AdminGroup, channel_warnings, deliver, reply

log = logging.getLogger(__name__)

MESSAGE_HELP = "訊息內容，可用變數：" + VARIABLES_HELP
MessageText = app_commands.Range[str, 1, config.MAX_MESSAGE_LENGTH]


class LeaveCog(commands.Cog, name="Leave"):
    leave = AdminGroup(name="離開", description="設定離開系統")

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
    @leave.command(name="快速設定", description="啟用離開系統並選擇頻道")
    @app_commands.describe(channel="發送離開訊息的頻道", message=MESSAGE_HELP, style="純文字或嵌入式訊息")
    @app_commands.rename(channel="頻道", message="訊息", style="樣式")
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
        text = f"✅ 已在 {channel.mention} 啟用離開訊息。"
        if notes:
            text += "\n" + "\n".join(f"⚠️ {note}" for note in notes)
        text += "\n可以用 `/離開 測試` 看看效果。"
        await reply(interaction, text)

    @leave.command(name="啟用", description="啟用離開系統")
    async def leave_enable(self, interaction: discord.Interaction) -> None:
        settings = database.get_settings("leave", interaction.guild_id)
        database.update_settings("leave", interaction.guild_id, enabled=1)
        if not settings["channel_id"]:
            await reply(interaction, "✅ 已啟用，但還沒有設定頻道，請用 `/離開 頻道` 選擇。")
        else:
            await reply(interaction, "✅ 離開系統已啟用。")

    @leave.command(name="停用", description="停用離開系統")
    async def leave_disable(self, interaction: discord.Interaction) -> None:
        database.update_settings("leave", interaction.guild_id, enabled=0)
        await reply(interaction, "✅ 離開系統已停用，設定會保留。")

    @leave.command(name="頻道", description="選擇離開頻道")
    @app_commands.describe(channel="發送離開訊息的頻道")
    @app_commands.rename(channel="頻道")
    async def leave_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        database.update_settings("leave", interaction.guild_id, channel_id=channel.id)
        settings = database.get_settings("leave", interaction.guild_id)
        notes = await channel_warnings(self.bot, channel, embeds=bool(settings["use_embed"]), files=False)
        text = f"✅ 離開訊息會發送到 {channel.mention}。"
        if notes:
            text += "\n" + "\n".join(f"⚠️ {note}" for note in notes)
        await reply(interaction, text)

    @leave.command(name="訊息", description="設定離開訊息內容與樣式（不填則顯示目前設定）")
    @app_commands.describe(text=MESSAGE_HELP, style="純文字或嵌入式訊息")
    @app_commands.rename(text="內容", style="樣式")
    @app_commands.choices(style=STYLE_CHOICES)
    async def leave_message(self, interaction: discord.Interaction, text: Optional[MessageText] = None,
                            style: Optional[app_commands.Choice[str]] = None) -> None:
        if text is None and style is None:
            settings = database.get_settings("leave", interaction.guild_id)
            kind = "嵌入式訊息" if settings["use_embed"] else "純文字"
            await reply(interaction, f"目前樣式：**{kind}**\n目前內容：\n```{settings['message']}```\n"
                                     f"可用變數：`{VARIABLES_HELP}`\n{VARIABLES_DESCRIPTION}\n輸入 `\\n` 可以換行。")
            return
        fields: dict = {}
        if text is not None:
            fields["message"] = clean_template(text)
        if style is not None:
            fields["use_embed"] = int(style.value == "embed")
        database.update_settings("leave", interaction.guild_id, **fields)
        await reply(interaction, "✅ 離開訊息已更新，可以用 `/離開 測試` 查看。")

    @leave.command(name="測試", description="把測試離開訊息發送到離開頻道")
    async def leave_test(self, interaction: discord.Interaction) -> None:
        settings = database.get_settings("leave", interaction.guild_id)
        if not settings["channel_id"]:
            await reply(interaction, "❌ 還沒有設定離開頻道，請先用 `/離開 頻道`。")
            return
        await interaction.response.defer(ephemeral=True)
        sent, notes = await self._send_leave(interaction.guild, interaction.user, settings)
        text = f"✅ 測試訊息已發送到 <#{settings['channel_id']}>。" if sent else "❌ 測試訊息發送失敗。"
        if notes:
            text += "\n" + "\n".join(f"⚠️ {note}" for note in notes)
        await reply(interaction, text)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LeaveCog(bot))
