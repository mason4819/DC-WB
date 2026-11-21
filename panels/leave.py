"""Leave page of the /設定 panel."""
import discord

import config
import database
from panels.base import DANGER, PRIMARY, SUCCESS, PanelView, go_home, make_button, on_off, reject
from panels.common import MessageModal, message_field, style_label
from panels.welcome import TEXT_CHANNELS
from utils.messaging import channel_warnings


class LeaveView(PanelView):
    @classmethod
    async def create(cls, bot, opener: discord.Interaction) -> "LeaveView":
        s = await database.get_settings("leave", opener.guild_id)
        view = cls(bot, opener, accent=config.LEAVE_COLOR)
        view.settings = s
        view.heading("離開系統", "成員離開時發送訊息")
        view.divider()
        view.item("狀態", on_off(s["enabled"]), "停用" if s["enabled"] else "啟用", view._toggle("enabled"),
                  style=DANGER if s["enabled"] else SUCCESS)
        view.item("訊息樣式", style_label(s["use_embed"]), "切換樣式", view._toggle("use_embed"))
        view.item("訊息內容", message_field(s["message"]), "編輯", view._edit_message, style=PRIMARY)
        view.divider()

        channel = opener.guild.get_channel(s["channel_id"]) if s["channel_id"] else None
        view.channel_menu = discord.ui.ChannelSelect(channel_types=TEXT_CHANNELS, placeholder="選擇離開頻道",
                                                     default_values=[channel] if channel is not None else [])
        view.channel_menu.callback = view._pick_channel
        view.select(view.channel_menu)
        view.divider()
        view.buttons(make_button("測試", view._test), make_button("返回", view._back))

        notes: list[str] = []
        if s["enabled"] and not s["channel_id"]:
            notes.append("還沒有選擇離開頻道，不會發送訊息。")
        elif s["channel_id"]:
            notes += await channel_warnings(bot, channel, embeds=bool(s["use_embed"]), files=False)
        view.notes(notes)
        view.footer()
        return view

    def _toggle(self, field: str):
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            await database.update_settings("leave", interaction.guild_id, **{field: 0 if self.settings[field] else 1})
            await self.go(interaction, await LeaveView.create(self.bot, self.opener))
        return callback

    async def _pick_channel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await database.update_settings("leave", interaction.guild_id, channel_id=self.channel_menu.values[0].id)
        await self.go(interaction, await LeaveView.create(self.bot, self.opener))

    async def _edit_message(self, interaction: discord.Interaction) -> None:
        async def rebuild() -> PanelView:
            self.stop()
            return await LeaveView.create(self.bot, self.opener)
        await interaction.response.send_modal(MessageModal("leave", "編輯離開訊息", self.settings["message"], rebuild))

    async def _test(self, interaction: discord.Interaction) -> None:
        settings = await database.get_settings("leave", interaction.guild_id)
        if not settings["channel_id"]:
            await reject(interaction, "還沒有選擇離開頻道，請先在面板上選一個頻道。")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        sent, notes = await self.bot.get_cog("Leave").send_leave(interaction.guild, interaction.user, settings)
        text = f"測試訊息已發送到 <#{settings['channel_id']}>。" if sent else "測試訊息發送失敗。"
        if notes:
            text += "\n" + "\n".join(f"注意：{note}" for note in notes)
        await interaction.followup.send(text, ephemeral=True)

    async def _back(self, interaction: discord.Interaction) -> None:
        await go_home(self, interaction)
