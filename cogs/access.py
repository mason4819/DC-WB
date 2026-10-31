"""/管理員: choose which members and roles may use the setup commands."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

import database
from utils.messaging import OwnerGroup, reply

log = logging.getLogger(__name__)

EXPLAIN_RULE = ("現在只有「伺服器擁有者、管理員（Administrator），以及名單內的成員或身分組」"
                "可以使用設定指令；單純擁有「管理伺服器」權限已不再自動通過。")


def _mentions(prefix: str, ids: list[int]) -> str:
    return "、".join(f"<{prefix}{i}>" for i in ids) if ids else "（無）"


class AccessCog(commands.Cog, name="Access"):
    manage = OwnerGroup(name="管理員", description="設定誰可以使用機器人的設定指令（僅擁有者與管理員）")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _add(self, interaction: discord.Interaction, kind: str, target_id: int, mention: str) -> None:
        had_list = any((await database.get_access(interaction.guild_id)).values())
        result = await database.add_access(interaction.guild_id, kind, target_id)
        if result == "exists":
            await reply(interaction, f"{mention} 已經在管理名單裡了。")
        elif result == "full":
            await reply(interaction, f"❌ 名單已滿（每種最多 {database.MAX_ACCESS_ENTRIES} 個），請先移除不需要的。")
        else:
            text = f"✅ 已把 {mention} 加入管理名單。"
            if not had_list:
                text += "\n" + EXPLAIN_RULE
            await reply(interaction, text)

    async def _remove(self, interaction: discord.Interaction, kind: str, target_id: int, mention: str) -> None:
        if not await database.remove_access(interaction.guild_id, kind, target_id):
            await reply(interaction, f"{mention} 不在管理名單裡。")
            return
        text = f"✅ 已把 {mention} 從管理名單移除。"
        if not any((await database.get_access(interaction.guild_id)).values()):
            text += "\n名單已經清空，恢復成「管理伺服器」或「管理員」權限即可使用設定指令。"
        await reply(interaction, text)

    @manage.command(name="新增成員", description="允許某位成員使用設定指令")
    @app_commands.describe(member="要加入管理名單的成員")
    @app_commands.rename(member="成員")
    async def add_member(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if member.bot:
            await reply(interaction, "❌ 不能把機器人加入管理名單。")
            return
        await self._add(interaction, "user", member.id, member.mention)

    @manage.command(name="新增身分組", description="允許擁有某個身分組的成員使用設定指令")
    @app_commands.describe(role="要加入管理名單的身分組")
    @app_commands.rename(role="身分組")
    async def add_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if role.is_default():
            await reply(interaction, "❌ 不能使用 @everyone，否則等於所有人都能使用。")
            return
        if role.managed:
            await reply(interaction, "❌ 這是由整合服務（例如機器人）管理的身分組，成員無法擁有，請選擇一般身分組。")
            return
        await self._add(interaction, "role", role.id, role.mention)

    @manage.command(name="移除成員", description="把成員從管理名單移除")
    @app_commands.describe(user="要移除的成員")
    @app_commands.rename(user="成員")
    async def remove_member(self, interaction: discord.Interaction, user: discord.User) -> None:
        await self._remove(interaction, "user", user.id, user.mention)

    @manage.command(name="移除身分組", description="把身分組從管理名單移除")
    @app_commands.describe(role="要移除的身分組")
    @app_commands.rename(role="身分組")
    async def remove_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await self._remove(interaction, "role", role.id, role.mention)

    @manage.command(name="列表", description="查看目前誰可以使用設定指令")
    async def show_list(self, interaction: discord.Interaction) -> None:
        access = await database.get_access(interaction.guild_id)
        if not access["user"] and not access["role"]:
            await reply(interaction, "目前**沒有設定管理名單**。\n擁有「管理伺服器」或「管理員」權限的成員都可以使用設定指令。")
            return
        await reply(interaction,
                    f"**管理名單**\n成員：{_mentions('@', access['user'])}\n身分組：{_mentions('@&', access['role'])}\n\n"
                    f"{EXPLAIN_RULE}\n伺服器擁有者與管理員永遠可以使用，不會被鎖在外面。")

    @manage.command(name="清空", description="清空管理名單，恢復成「管理伺服器」權限即可使用")
    async def clear_list(self, interaction: discord.Interaction) -> None:
        await database.clear_access(interaction.guild_id)
        await reply(interaction, "✅ 管理名單已清空。擁有「管理伺服器」或「管理員」權限的成員可以使用設定指令。")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AccessCog(bot))
