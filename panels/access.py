"""Access list page of the /設定 panel: who may open the panel."""
import discord

import database
from panels.base import PanelView, go_home, make_embed, reject
from utils.access import is_owner_or_admin


def _mentions(prefix: str, ids: list[int]) -> str:
    return "、".join(f"<{prefix}{i}>" for i in ids) if ids else "（無）"


class AccessView(PanelView):
    @classmethod
    async def create(cls, bot, opener: discord.Interaction) -> "AccessView":
        view = cls(bot, opener)
        guild = opener.guild
        access = await database.get_access(opener.guild_id)
        view.access = access
        view.can_edit = is_owner_or_admin(opener)
        if access["user"] or access["role"]:
            rule = ("現在只有「伺服器擁有者、管理員（Administrator），以及下面名單內的成員或身分組」可以使用 /設定；"
                    "單純擁有「管理伺服器」權限已不再自動通過。")
        else:
            rule = "目前**沒有設定名單**：擁有「管理伺服器」或「管理員」權限的成員都可以使用 /設定。"
        embed = make_embed("🔑 權限名單", rule)
        embed.add_field(name="成員", value=_mentions("@", access["user"]), inline=False)
        embed.add_field(name="身分組", value=_mentions("@&", access["role"]), inline=False)
        embed.add_field(name="注意", inline=False, value=(
            "伺服器擁有者與管理員永遠可以使用，不會被鎖在外面。\n"
            + ("用下面的選單挑出名單；選單清空就是移除名單。" if view.can_edit
               else "只有伺服器擁有者與管理員可以修改名單，你目前只能查看。")))
        view.embed = embed

        users = [discord.SelectDefaultValue(id=i, type=discord.SelectDefaultValueType.user) for i in access["user"]]
        roles = [guild.get_role(i) for i in access["role"] if guild.get_role(i) is not None]
        user_menu = discord.ui.UserSelect(placeholder="選擇可以使用 /設定 的成員", min_values=0,
                                          max_values=database.MAX_ACCESS_ENTRIES, row=0,
                                          default_values=users, disabled=not view.can_edit)
        user_menu.callback = view._pick_users
        view.user_menu = user_menu
        view.add_item(user_menu)
        role_menu = discord.ui.RoleSelect(placeholder="選擇可以使用 /設定 的身分組", min_values=0,
                                          max_values=database.MAX_ACCESS_ENTRIES, row=1,
                                          default_values=roles, disabled=not view.can_edit)
        role_menu.callback = view._pick_roles
        view.role_menu = role_menu
        view.add_item(role_menu)
        view.add_button("清空名單", view._clear, row=2, disabled=not view.can_edit or not (access["user"] or access["role"]))
        view.add_button("返回", view._back, row=2, emoji="↩️")
        return view

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if is_owner_or_admin(interaction):
            return True
        await reject(interaction, "❌ 只有伺服器擁有者與管理員可以修改名單。")
        return False

    async def _pick_users(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        await interaction.response.defer()
        people = [u for u in self.user_menu.values if not u.bot]
        await database.set_access(interaction.guild_id, "user", [u.id for u in people])
        await self.go(interaction, await AccessView.create(self.bot, self.opener))
        if len(people) != len(self.user_menu.values):
            await interaction.followup.send("機器人帳號不能加入名單，已略過。", ephemeral=True)

    async def _pick_roles(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        await interaction.response.defer()
        usable = [r for r in self.role_menu.values if not (r.is_default() or r.managed)]
        await database.set_access(interaction.guild_id, "role", [r.id for r in usable])
        await self.go(interaction, await AccessView.create(self.bot, self.opener))
        if len(usable) != len(self.role_menu.values):
            await interaction.followup.send("@everyone 和由整合服務管理的身分組不能加入名單，已略過。", ephemeral=True)

    async def _clear(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        await interaction.response.defer()
        await database.clear_access(interaction.guild_id)
        await self.go(interaction, await AccessView.create(self.bot, self.opener))

    async def _back(self, interaction: discord.Interaction) -> None:
        await go_home(self, interaction)
