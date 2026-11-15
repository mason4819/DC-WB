"""Auto roles: gives new members up to 10 roles and tells admins when that fails."""
import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import database
from utils.messaging import AdminGroup, get_bot_member, reply, role_problem

log = logging.getLogger(__name__)

NOTICE_COOLDOWN = 6 * 60 * 60  # post at most one problem notice per server every 6 hours


class AutoRoleCog(commands.Cog, name="AutoRole"):
    autorole = AdminGroup(name="自動身分組", description="設定新成員加入時自動取得的身分組（可以設定多個）")

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
                    problems.append(f"身分組 `{role_id}` 已經不存在，請用 `/自動身分組 移除` 或 `/自動身分組 設定` 清理。")
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
            await channel.send(f"⚠️ **自動身分組發生問題：**{text}\n"
                               "*（這則提醒給管理員看，每 6 小時最多出現一次。）*",
                               allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.info("Could not post the auto role notice in guild %s", guild.id)

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _unusable(role: discord.Role) -> bool:
        return role.is_default() or role.managed

    async def _problems_text(self, guild: discord.Guild, role_ids: list[int]) -> str:
        me = await get_bot_member(self.bot, guild)
        lines = []
        for role_id in role_ids:
            role = guild.get_role(role_id)
            problem = f"身分組 `{role_id}` 已經不存在。" if role is None else (role_problem(me, role) if me else None)
            if problem:
                lines.append(f"⚠️ {problem}")
        return "\n".join(lines)

    # --------------------------------------------------------------- commands
    @autorole.command(name="新增", description="新增新成員自動取得的身分組（一次最多 5 個，總共最多 10 個）")
    @app_commands.describe(role="身分組", role2="再加一個（選填）", role3="再加一個（選填）",
                           role4="再加一個（選填）", role5="再加一個（選填）")
    @app_commands.rename(role="身分組", role2="身分組2", role3="身分組3", role4="身分組4", role5="身分組5")
    async def autorole_add(self, interaction: discord.Interaction, role: discord.Role,
                           role2: Optional[discord.Role] = None, role3: Optional[discord.Role] = None,
                           role4: Optional[discord.Role] = None, role5: Optional[discord.Role] = None) -> None:
        added, skipped = [], []
        for item in dict.fromkeys(r for r in (role, role2, role3, role4, role5) if r is not None):
            if self._unusable(item):
                skipped.append(f"{item.mention}（@everyone 或由整合服務管理的身分組，機器人無法發放）")
                continue
            result = await database.add_autorole(interaction.guild_id, item.id)
            if result == "added":
                added.append(item.mention)
            elif result == "exists":
                skipped.append(f"{item.mention}（已經在清單裡）")
            else:
                skipped.append(f"{item.mention}（清單已滿，最多 {database.MAX_AUTOROLES} 個）")
        if added:
            await database.update_settings("autorole", interaction.guild_id, enabled=1)
        ids = await database.get_autoroles(interaction.guild_id)
        text = (f"✅ 已新增：{'、'.join(added)}\n" if added else "") + \
               (f"略過：{'；'.join(skipped)}\n" if skipped else "") + f"目前共 {len(ids)} 個自動身分組。"
        problems = await self._problems_text(interaction.guild, ids) if added else ""
        await reply(interaction, text + (f"\n{problems}" if problems else ""))

    @autorole.command(name="設定", description="只使用這一個身分組（會取代原本的整份清單）")
    @app_commands.describe(role="要發給每位新成員的身分組")
    @app_commands.rename(role="身分組")
    async def autorole_set(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if self._unusable(role):
            await reply(interaction, "❌ 機器人無法發放這個身分組，請選擇一般的身分組。")
            return
        await database.set_autoroles(interaction.guild_id, [role.id])
        await database.update_settings("autorole", interaction.guild_id, enabled=1)
        problems = await self._problems_text(interaction.guild, [role.id])
        await reply(interaction, f"✅ 自動身分組現在只有 {role.mention}（已取代原本的清單）。" + (f"\n{problems}" if problems else ""))

    @autorole.command(name="移除", description="移除一個自動身分組；不選身分組就是全部移除")
    @app_commands.describe(role="要移除的身分組（不填 = 全部移除並停用）")
    @app_commands.rename(role="身分組")
    async def autorole_remove(self, interaction: discord.Interaction, role: Optional[discord.Role] = None) -> None:
        removed = await database.remove_autorole(interaction.guild_id, role.id if role else None)
        left = await database.get_autoroles(interaction.guild_id)
        if not left:
            await database.update_settings("autorole", interaction.guild_id, enabled=0)
        if role is None:
            await reply(interaction, f"✅ 已移除全部 {removed} 個自動身分組，新成員不會再自動取得身分組。" if removed
                        else "目前沒有設定任何自動身分組。")
        elif removed:
            await reply(interaction, f"✅ 已移除 {role.mention}。" + (f"還剩 {len(left)} 個。" if left else "清單已空，自動身分組已停用。"))
        else:
            await reply(interaction, f"{role.mention} 不在自動身分組清單裡。")

    @autorole.command(name="列表", description="查看目前的自動身分組清單")
    async def autorole_list(self, interaction: discord.Interaction) -> None:
        settings = await database.get_settings("autorole", interaction.guild_id)
        ids = await database.get_autoroles(interaction.guild_id)
        if not ids:
            await reply(interaction, "目前沒有設定自動身分組。用 `/自動身分組 新增` 開始。")
            return
        lines = [f"{n}. " + (interaction.guild.get_role(i).mention if interaction.guild.get_role(i) else f"（已不存在）`{i}`")
                 for n, i in enumerate(ids, start=1)]
        problems = await self._problems_text(interaction.guild, ids)
        await reply(interaction, f"**自動身分組**（{'✅ 啟用' if settings['enabled'] else '❌ 停用'}，{len(ids)}/{database.MAX_AUTOROLES}）\n"
                                 + "\n".join(lines) + (f"\n\n{problems}" if problems else ""))

    @autorole.command(name="啟用", description="啟用自動身分組")
    async def autorole_enable(self, interaction: discord.Interaction) -> None:
        ids = await database.get_autoroles(interaction.guild_id)
        if not ids:
            await reply(interaction, "❌ 還沒有設定身分組，請先用 `/自動身分組 新增`。")
            return
        await database.update_settings("autorole", interaction.guild_id, enabled=1)
        problems = await self._problems_text(interaction.guild, ids)
        await reply(interaction, f"✅ 自動身分組已啟用（{len(ids)} 個）。" + (f"\n{problems}" if problems else ""))

    @autorole.command(name="停用", description="停用自動身分組")
    async def autorole_disable(self, interaction: discord.Interaction) -> None:
        await database.update_settings("autorole", interaction.guild_id, enabled=0)
        await reply(interaction, "✅ 自動身分組已停用，清單會保留。")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoRoleCog(bot))
