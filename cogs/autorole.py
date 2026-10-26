"""Auto role: gives new members a role and tells admins when that fails."""
import logging
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands

import database
from utils.messaging import AdminGroup, get_bot_member, reply, role_problem

log = logging.getLogger(__name__)

NOTICE_COOLDOWN = 6 * 60 * 60  # post at most one problem notice per server every 6 hours


class AutoRoleCog(commands.Cog, name="AutoRole"):
    autorole = AdminGroup(name="自動身分組", description="設定新成員加入時自動取得的身分組")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_notice: dict[int, float] = {}  # guild_id -> monotonic time, tiny

    # ------------------------------------------------------------------ events
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        try:
            settings = database.get_settings("autorole", guild.id)
            if not settings["enabled"] or not settings["role_id"]:
                return

            role = guild.get_role(settings["role_id"])
            if role is None:
                await self._notify(guild, "設定的自動身分組已經不存在，請用 `/自動身分組 設定` 重新選擇。")
                return

            me = await get_bot_member(self.bot, guild)
            problem = role_problem(me, role) if me else "無法確認機器人自己的權限。"
            if problem:
                await self._notify(guild, f"無法把 {role.mention} 發給 {member.mention}。{problem}")
                return

            try:
                await member.add_roles(role, reason="Auto role on join")
            except discord.Forbidden:
                await self._notify(guild, f"Discord 拒絕發放 {role.mention}，請檢查機器人的「管理身分組」權限與身分組順序。")
            except discord.NotFound:
                log.info("Member %s left before the auto role was applied", member.id)
            except discord.HTTPException:
                log.warning("Discord API error while giving the auto role in guild %s", guild.id, exc_info=True)
        except sqlite3.Error:
            log.exception("Database error in auto role for guild %s", guild.id)
        except Exception:
            log.exception("Unexpected auto role error in guild %s", guild.id)

    async def _notify(self, guild: discord.Guild, text: str) -> None:
        """Log the problem and post a short notice for admins (rate limited)."""
        log.warning("Auto role problem in guild %s: %s", guild.id, text)
        now = time.monotonic()
        if now - self._last_notice.get(guild.id, float("-inf")) < NOTICE_COOLDOWN:
            return
        self._last_notice[guild.id] = now

        channel = None
        try:
            welcome = database.get_settings("welcome", guild.id)
            if welcome["channel_id"]:
                channel = guild.get_channel(welcome["channel_id"])
        except sqlite3.Error:
            pass
        if not isinstance(channel, discord.TextChannel):
            channel = guild.system_channel
        if channel is None:
            return
        try:
            await channel.send(f"⚠️ **自動身分組發生問題：**{text}\n"
                               "*（這則提醒給管理員看，每 6 小時最多出現一次。）*",
                               allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.info("Could not post the auto role notice in guild %s", guild.id)

    # --------------------------------------------------------------- commands
    @autorole.command(name="設定", description="選擇新成員加入時自動取得的身分組")
    @app_commands.describe(role="要發給每位新成員的身分組")
    @app_commands.rename(role="身分組")
    async def autorole_set(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if role.is_default() or role.managed:
            await reply(interaction, "❌ 機器人無法發放這個身分組，請選擇一般的身分組。")
            return
        database.update_settings("autorole", interaction.guild_id, role_id=role.id, enabled=1)
        me = await get_bot_member(self.bot, interaction.guild)
        problem = role_problem(me, role) if me else None
        text = f"✅ 新成員會自動取得 {role.mention}。"
        if problem:
            text += f"\n⚠️ 目前還無法運作：{problem}"
        await reply(interaction, text)

    @autorole.command(name="移除", description="移除自動身分組設定")
    async def autorole_remove(self, interaction: discord.Interaction) -> None:
        database.update_settings("autorole", interaction.guild_id, role_id=None, enabled=0)
        await reply(interaction, "✅ 已移除自動身分組，新成員不會再自動取得身分組。")

    @autorole.command(name="啟用", description="啟用自動身分組")
    async def autorole_enable(self, interaction: discord.Interaction) -> None:
        settings = database.get_settings("autorole", interaction.guild_id)
        role = interaction.guild.get_role(settings["role_id"]) if settings["role_id"] else None
        if role is None:
            await reply(interaction, "❌ 還沒有設定身分組，請先用 `/自動身分組 設定`。")
            return
        database.update_settings("autorole", interaction.guild_id, enabled=1)
        me = await get_bot_member(self.bot, interaction.guild)
        problem = role_problem(me, role) if me else None
        text = f"✅ 自動身分組已啟用（{role.mention}）。"
        if problem:
            text += f"\n⚠️ 目前還無法運作：{problem}"
        await reply(interaction, text)

    @autorole.command(name="停用", description="停用自動身分組")
    async def autorole_disable(self, interaction: discord.Interaction) -> None:
        database.update_settings("autorole", interaction.guild_id, enabled=0)
        await reply(interaction, "✅ 自動身分組已停用，選好的身分組會保留。")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoRoleCog(bot))
