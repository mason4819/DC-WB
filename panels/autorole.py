"""Auto role page of the /設定 panel."""
import discord

import database
from panels.base import DANGER, SUCCESS, PanelView, go_home, make_button, on_off, reject
from utils.messaging import get_bot_member, role_problem


async def role_problems(bot, guild: discord.Guild, role_ids: list[int]) -> list[str]:
    """Why some of the chosen roles could not be given (deleted, above the bot, managed...)."""
    me = await get_bot_member(bot, guild)
    lines = []
    for role_id in role_ids:
        role = guild.get_role(role_id)
        problem = f"身分組 `{role_id}` 已經不存在，重新選擇一次就會清掉。" if role is None else (
            role_problem(me, role) if me else None)
        if problem:
            lines.append(problem)
    return lines


class AutoRoleView(PanelView):
    @classmethod
    async def create(cls, bot, opener: discord.Interaction) -> "AutoRoleView":
        view = cls(bot, opener)
        guild = opener.guild
        view.settings = s = await database.get_settings("autorole", opener.guild_id)
        view.role_ids = ids = await database.get_autoroles(opener.guild_id)
        view.heading("自動身分組", "新成員加入時自動取得的身分組")
        view.divider()
        view.item("狀態", on_off(s["enabled"]), "停用" if s["enabled"] else "啟用", view._toggle,
                  style=DANGER if s["enabled"] else SUCCESS)
        listing = "\n".join(f"- {guild.get_role(i).mention}" if guild.get_role(i) else f"- 已不存在 `{i}`" for i in ids)
        view.item(f"身分組（{len(ids)}/{database.MAX_AUTOROLES}）", listing or "尚未選擇", "清空名單", view._clear,
                  disabled=not ids)
        view.divider()

        existing = [guild.get_role(i) for i in ids if guild.get_role(i) is not None]
        view.menu = discord.ui.RoleSelect(placeholder="選擇新成員自動取得的身分組", min_values=0,
                                          max_values=database.MAX_AUTOROLES, default_values=existing)
        view.menu.callback = view._pick
        view.select(view.menu)
        view.divider()
        view.buttons(make_button("返回", view._back))

        problems = await role_problems(bot, guild, ids) if ids else []
        if s["enabled"] and not ids:
            problems.append("已啟用但清單是空的，請先選擇身分組。")
        view.notes(problems)
        view.footer()
        return view

    async def _pick(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        usable = [role for role in self.menu.values if not (role.is_default() or role.managed)]
        skipped = [role.mention for role in self.menu.values if role.is_default() or role.managed]
        await database.set_autoroles(interaction.guild_id, [role.id for role in usable])
        await database.update_settings("autorole", interaction.guild_id, enabled=int(bool(usable)))
        await self.go(interaction, await AutoRoleView.create(self.bot, self.opener))
        if skipped:
            await interaction.followup.send("略過（@everyone 或由整合服務管理的身分組，機器人無法發放）：" + "、".join(skipped),
                                            ephemeral=True)

    async def _toggle(self, interaction: discord.Interaction) -> None:
        if not self.settings["enabled"] and not self.role_ids:
            await reject(interaction, "還沒有選擇身分組，請先用選單挑出要自動發放的身分組。")
            return
        await interaction.response.defer()
        await database.update_settings("autorole", interaction.guild_id, enabled=0 if self.settings["enabled"] else 1)
        await self.go(interaction, await AutoRoleView.create(self.bot, self.opener))

    async def _clear(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await database.remove_autorole(interaction.guild_id, None)
        await database.update_settings("autorole", interaction.guild_id, enabled=0)
        await self.go(interaction, await AutoRoleView.create(self.bot, self.opener))

    async def _back(self, interaction: discord.Interaction) -> None:
        await go_home(self, interaction)
