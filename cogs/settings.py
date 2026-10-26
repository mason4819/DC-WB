"""/設定: one overview of every setting, including detected problems."""
import discord
from discord import app_commands
from discord.ext import commands

import config
import database
from utils.messaging import channel_warnings, get_bot_member, role_problem


def _status(enabled: bool) -> str:
    return "✅ 已啟用" if enabled else "❌ 已停用"


def _channel_text(channel_id) -> str:
    return f"<#{channel_id}>" if channel_id else "尚未設定"


def _short(text: str, limit: int = 180) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


class SettingsCog(commands.Cog, name="Settings"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="設定", description="查看歡迎、離開與自動身分組的所有設定")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def show_config(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        welcome = database.get_settings("welcome", guild.id)
        leave = database.get_settings("leave", guild.id)
        autorole = database.get_settings("autorole", guild.id)
        problems: list[str] = []

        embed = discord.Embed(title=f"{guild.name} 的設定", colour=config.WELCOME_COLOR)

        embed.add_field(
            name="歡迎系統",
            value=(f"{_status(welcome['enabled'])}\n頻道：{_channel_text(welcome['channel_id'])}\n"
                   f"樣式：{'嵌入式訊息' if welcome['use_embed'] else '純文字'}\n"
                   f"圖片：{'開啟' if welcome['image_enabled'] else '關閉'}\n"
                   f"內容：`{_short(welcome['message'])}`"),
            inline=False)
        if welcome["enabled"]:
            problems += await channel_warnings(
                self.bot, guild.get_channel(welcome["channel_id"]) if welcome["channel_id"] else None,
                embeds=bool(welcome["use_embed"]), files=bool(welcome["image_enabled"]))

        embed.add_field(
            name="離開系統",
            value=(f"{_status(leave['enabled'])}\n頻道：{_channel_text(leave['channel_id'])}\n"
                   f"樣式：{'嵌入式訊息' if leave['use_embed'] else '純文字'}\n"
                   f"內容：`{_short(leave['message'])}`"),
            inline=False)
        if leave["enabled"]:
            problems += await channel_warnings(
                self.bot, guild.get_channel(leave["channel_id"]) if leave["channel_id"] else None,
                embeds=bool(leave["use_embed"]), files=False)

        role = guild.get_role(autorole["role_id"]) if autorole["role_id"] else None
        embed.add_field(
            name="自動身分組",
            value=f"{_status(autorole['enabled'])}\n身分組：{role.mention if role else '尚未設定'}",
            inline=False)
        if autorole["enabled"]:
            if role is None:
                problems.append("自動身分組已經不存在，請用 `/自動身分組 設定` 重新選擇。")
            else:
                me = await get_bot_member(self.bot, guild)
                problem = role_problem(me, role) if me else None
                if problem:
                    problems.append(problem)

        if problems:
            embed.add_field(name="⚠️ 發現的問題", value="\n".join(f"• {p}" for p in problems)[:1000],
                            inline=False)
            embed.colour = config.WARNING_COLOR

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SettingsCog(bot))
