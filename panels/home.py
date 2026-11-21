"""Home page of the /設定 panel: an overview of every setting, with a button next to each area."""
import discord

import config
import database
from panels.access import AccessView
from panels.autorole import AutoRoleView, role_problems
from panels.base import PRIMARY, PanelView, make_button, on_off, require_bot_owner, short
from panels.common import style_label
from panels.leave import LeaveView
from panels.welcome import DELIVERY_LABELS, WelcomeView, background_label
from utils import image as imaging
from utils.messaging import channel_warnings


class HomeView(PanelView):
    @classmethod
    async def create(cls, bot, opener: discord.Interaction) -> "HomeView":
        guild = opener.guild
        welcome = await database.get_settings("welcome", guild.id)
        leave = await database.get_settings("leave", guild.id)
        autorole = await database.get_settings("autorole", guild.id)
        role_ids = await database.get_autoroles(guild.id)
        access = await database.get_access(guild.id)
        problems: list[str] = []
        view = cls(bot, opener)

        view.heading("設定", short(guild.name, 60))
        view.divider()
        channel = f"<#{welcome['channel_id']}>" if welcome["channel_id"] else "尚未設定頻道"
        view.item("歡迎系統", (
            f"{on_off(welcome['enabled'])}・{DELIVERY_LABELS[welcome['delivery']]}・{channel}\n"
            f"{style_label(welcome['use_embed'])}・圖片{'開' if welcome['image_enabled'] else '關'}"
            f"・{imaging.get_theme(welcome['theme'])['label']}・{background_label(welcome)}"),
            "設定", view._open(WelcomeView), style=PRIMARY)
        if welcome["enabled"] and welcome["delivery"] in ("channel", "both"):
            if not welcome["channel_id"]:
                problems.append("歡迎系統還沒有選擇頻道。")
            else:
                problems += await channel_warnings(bot, guild.get_channel(welcome["channel_id"]),
                                                   embeds=bool(welcome["use_embed"]), files=bool(welcome["image_enabled"]))

        leave_channel = f"<#{leave['channel_id']}>" if leave["channel_id"] else "尚未設定頻道"
        view.item("離開系統", f"{on_off(leave['enabled'])}・{leave_channel}・{style_label(leave['use_embed'])}",
                  "設定", view._open(LeaveView), style=PRIMARY)
        if leave["enabled"]:
            if not leave["channel_id"]:
                problems.append("離開系統還沒有選擇頻道。")
            else:
                problems += await channel_warnings(bot, guild.get_channel(leave["channel_id"]),
                                                   embeds=bool(leave["use_embed"]), files=False)

        mentions = [guild.get_role(i).mention if guild.get_role(i) else "已不存在" for i in role_ids]
        view.item("自動身分組", f"{on_off(autorole['enabled'])}・{len(role_ids)}/{database.MAX_AUTOROLES}"
                  f"・{'、'.join(mentions) or '尚未選擇'}", "設定", view._open(AutoRoleView), style=PRIMARY)
        if autorole["enabled"] and not role_ids:
            problems.append("自動身分組已啟用但清單是空的。")
        problems += await role_problems(bot, guild, role_ids) if role_ids else []

        if access["user"] or access["role"]:
            rule = f"限定名單：{len(access['user'])} 位成員、{len(access['role'])} 個身分組"
        else:
            rule = "未設定名單：有「管理伺服器」或「管理員」權限即可使用"
        view.item("權限名單", rule, "設定", view._open(AccessView))
        view.item("後臺", "機器人擁有者專用", "進入", view._backend)
        view.divider()
        view.notes(problems, "發現的問題")
        view.buttons(make_button("重新整理", view._refresh), make_button("機器人首頁", url=config.HOMEPAGE_URL))
        view.footer()
        if problems:
            view.box.accent_colour = config.WARNING_COLOR
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
