"""Auto roles: gives new members up to 10 roles and tells admins when that fails.

The list is chosen in the /設定 panel (panels/autorole.py)."""
import logging
import time

import discord
from discord.ext import commands

import database
from utils.messaging import get_bot_member, role_problem

log = logging.getLogger(__name__)

NOTICE_COOLDOWN = 6 * 60 * 60  # post at most one problem notice per server every 6 hours


class AutoRoleCog(commands.Cog, name="AutoRole"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_notice: dict[int, float] = {}  # guild_id -> monotonic time, tiny

    # ------------------------------------------------------------------ events
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        try:
            settings = await database.get_settings("autorole", guild.id)
            if not settings["enabled"]:
                return
            role_ids = await database.get_autoroles(guild.id)
            if not role_ids:
                return

            me = await get_bot_member(self.bot, guild)
            usable: list[discord.Role] = []
            problems: list[str] = []
            for role_id in role_ids:
                role = guild.get_role(role_id)
                if role is None:
                    problems.append(f"身分組 `{role_id}` 已經不存在，請到 /設定 → 自動身分組 重新選擇來清理。")
                    continue
                problem = role_problem(me, role) if me else "無法確認機器人自己的權限。"
                if problem:
                    problems.append(problem)
                else:
                    usable.append(role)

            if usable:    # one API call for all roles; the ones that work are still given if others are broken
                try:
                    await member.add_roles(*usable, reason="Auto role on join")
                except discord.Forbidden:
                    problems.append("Discord 拒絕發放身分組，請檢查機器人的「管理身分組」權限與身分組順序。")
                except discord.NotFound:
                    log.info("Member %s left before the auto roles were applied", member.id)
                except discord.HTTPException:
                    log.warning("Discord API error while giving auto roles in guild %s", guild.id, exc_info=True)
            if problems:
                await self._notify(guild, f"新成員 {member.mention} 的自動身分組沒有全部發放成功：\n" + "\n".join(f"• {p}" for p in problems))
        except database.DatabaseError:
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
            welcome = await database.get_settings("welcome", guild.id)
            if welcome["channel_id"]:
                channel = guild.get_channel(welcome["channel_id"])
        except database.DatabaseError:
            pass
        if not isinstance(channel, discord.TextChannel):
            channel = guild.system_channel
        if channel is None:
            return
        try:
            await channel.send(f"**自動身分組發生問題：**{text}\n"
                               "*（這則提醒給管理員看，每 6 小時最多出現一次。）*",
                               allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.info("Could not post the auto role notice in guild %s", guild.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoRoleCog(bot))
