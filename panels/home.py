"""Home page of the /設定 panel: an overview of every setting, with a button for each area."""
import discord

import config
import database
from panels.autorole import AutoRoleView, role_problems
from panels.access import AccessView
from panels.base import PanelView, make_embed, require_bot_owner
from panels.leave import LeaveView
from panels.welcome import DELIVERY_LABELS, WelcomeView, background_label
from panels.common import style_label
from utils import image as imaging
from utils.messaging import channel_warnings


def _status(enabled) -> str:
    return "✅ 已啟用" if enabled else "❌ 已停用"


def _short(text: str, limit: int = 120) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 1] + "…"


async def overview_embed(bot, guild: discord.Guild) -> discord.Embed:
    welcome = await database.get_settings("welcome", guild.id)
    leave = await database.get_settings("leave", guild.id)
    autorole = await database.get_settings("autorole", guild.id)
    role_ids = await database.get_autoroles(guild.id)
    access = await database.get_access(guild.id)
    problems: list[str] = []

    embed = make_embed(f"⚙️ {_short(guild.name, 60)} 的設定", "按下面的按鈕進入各項設定。")
    channel = f"<#{welcome['channel_id']}>" if welcome["channel_id"] else "尚未設定"
    embed.add_field(name="🎉 歡迎系統", inline=False, value=(
        f"{_status(welcome['enabled'])}・{DELIVERY_LABELS[welcome['delivery']]}・{channel}\n"
        f"{style_label(welcome['use_embed'])}・圖片{'開' if welcome['image_enabled'] else '關'}"
        f"（{imaging.get_theme(welcome['theme'])['label']}・{background_label(welcome)}）\n"
        f"內容：`{_short(welcome['message'])}`"))
    if welcome["enabled"] and welcome["delivery"] in ("channel", "both"):
        if not welcome["channel_id"]:
            problems.append("歡迎系統還沒有選擇頻道。")
        else:
            problems += await channel_warnings(bot, guild.get_channel(welcome["channel_id"]),
                                               embeds=bool(welcome["use_embed"]), files=bool(welcome["image_enabled"]))

    leave_channel = f"<#{leave['channel_id']}>" if leave["channel_id"] else "尚未設定"
    embed.add_field(name="👋 離開系統", inline=False, value=(
        f"{_status(leave['enabled'])}・{leave_channel}・{style_label(leave['use_embed'])}\n"
        f"內容：`{_short(leave['message'])}`"))
    if leave["enabled"]:
        if not leave["channel_id"]:
            problems.append("離開系統還沒有選擇頻道。")
        else:
            problems += await channel_warnings(bot, guild.get_channel(leave["channel_id"]),
                                               embeds=bool(leave["use_embed"]), files=False)

    mentions = [guild.get_role(i).mention if guild.get_role(i) else f"（已不存在）`{i}`" for i in role_ids]
    embed.add_field(name="🏷️ 自動身分組", inline=False, value=(
        f"{_status(autorole['enabled'])}・{len(role_ids)}/{database.MAX_AUTOROLES}：{'、'.join(mentions) or '尚未選擇'}"))
    if autorole["enabled"] and not role_ids:
        problems.append("自動身分組已啟用但清單是空的。")
    problems += await role_problems(bot, guild, role_ids) if role_ids else []

    if access["user"] or access["role"]:
        rule = f"限定名單：{len(access['user'])} 位成員、{len(access['role'])} 個身分組（擁有者與管理員永遠可以使用）"
    else:
        rule = "未設定名單：擁有「管理伺服器」或「管理員」權限即可使用"
    embed.add_field(name="🔑 權限名單", value=rule, inline=False)

    if problems:
        embed.add_field(name="⚠️ 發現的問題", value="\n".join(f"• {p}" for p in problems)[:1000], inline=False)
        embed.colour = config.WARNING_COLOR
    return embed


class HomeView(PanelView):
    @classmethod
    async def create(cls, bot, opener: discord.Interaction) -> "HomeView":
        view = cls(bot, opener)
        view.embed = await overview_embed(bot, opener.guild)
        view.add_button("歡迎系統", view._open(WelcomeView), row=0, emoji="🎉", style=discord.ButtonStyle.primary)
        view.add_button("離開系統", view._open(LeaveView), row=0, emoji="👋", style=discord.ButtonStyle.primary)
        view.add_button("自動身分組", view._open(AutoRoleView), row=0, emoji="🏷️", style=discord.ButtonStyle.primary)
        view.add_button("權限名單", view._open(AccessView), row=0, emoji="🔑")
        view.add_button("後臺", view._backend, row=0, emoji="🛠️")
        view.add_button("重新整理", view._refresh, row=1, emoji="🔄")
        view.add_item(discord.ui.Button(label="機器人首頁", url=config.HOMEPAGE_URL, row=1))
        return view

    def _open(self, page_class):
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            await self.go(interaction, await page_class.create(self.bot, self.opener))
        return callback

    async def _backend(self, interaction: discord.Interaction) -> None:
        if not await require_bot_owner(interaction):          # anyone not in OWNER_IDS gets an error here
            return
        from panels.backend import BackendHomeView            # imported here: it is big and only the owner needs it
        await interaction.response.defer()
        await self.go(interaction, await BackendHomeView.create(self.bot, self.opener))

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.go(interaction, await HomeView.create(self.bot, self.opener))
