"""Leave page of the /設定 panel."""
import discord

import config
import database
from panels.base import PanelView, go_home, make_embed, reject
from panels.common import MessageModal, message_field, style_label
from panels.welcome import TEXT_CHANNELS
from utils.messaging import channel_warnings


class LeaveView(PanelView):
    @classmethod
    async def create(cls, bot, opener: discord.Interaction) -> "LeaveView":
        view = cls(bot, opener)
        view.settings = s = await database.get_settings("leave", opener.guild_id)
        embed = make_embed("👋 離開系統", "✅ 已啟用" if s["enabled"] else "❌ 已停用", colour=config.LEAVE_COLOR)
        embed.add_field(name="頻道", value=f"<#{s['channel_id']}>" if s["channel_id"] else "尚未設定")
        embed.add_field(name="訊息樣式", value=style_label(s["use_embed"]))
        embed.add_field(name="訊息內容", value=message_field(s["message"]), inline=False)
        notes: list[str] = []
        if s["enabled"] and not s["channel_id"]:
            notes.append("還沒有選擇離開頻道，不會發送訊息。")
        elif s["channel_id"]:
            notes += await channel_warnings(bot, opener.guild.get_channel(s["channel_id"]),
                                            embeds=bool(s["use_embed"]), files=False)
        if notes:
            embed.add_field(name="⚠️ 提醒", value="\n".join(f"• {n}" for n in notes)[:1000], inline=False)
        view.embed = embed

        view.add_button("停用" if s["enabled"] else "啟用", view._toggle("enabled"), row=0,
                        style=discord.ButtonStyle.danger if s["enabled"] else discord.ButtonStyle.success)
        view.add_button("改成純文字" if s["use_embed"] else "改成嵌入式", view._toggle("use_embed"), row=0)
        view.add_button("編輯訊息內容", view._edit_message, row=0, emoji="✏️", style=discord.ButtonStyle.primary)

        channel = opener.guild.get_channel(s["channel_id"]) if s["channel_id"] else None
        menu = discord.ui.ChannelSelect(channel_types=TEXT_CHANNELS, placeholder="選擇離開頻道", row=1,
                                        default_values=[channel] if channel is not None else [])
        menu.callback = view._pick_channel
        view.channel_menu = menu
        view.add_item(menu)

        view.add_button("測試", view._test, row=2, emoji="🧪")
        view.add_button("返回", view._back, row=2, emoji="↩️")
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
            await reject(interaction, "❌ 還沒有選擇離開頻道，請先在面板上選一個頻道。")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        sent, notes = await self.bot.get_cog("Leave").send_leave(interaction.guild, interaction.user, settings)
        text = f"✅ 測試訊息已發送到 <#{settings['channel_id']}>。" if sent else "❌ 測試訊息發送失敗。"
        if notes:
            text += "\n" + "\n".join(f"⚠️ {note}" for note in notes)
        await interaction.followup.send(text, ephemeral=True)

    async def _back(self, interaction: discord.Interaction) -> None:
        await go_home(self, interaction)
