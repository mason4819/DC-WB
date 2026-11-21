"""Pieces shared by the welcome and leave pages."""
from typing import Awaitable, Callable

import discord

import config
import database
from panels.base import PanelModal, PanelView, reject, show
from utils.formatter import VARIABLES_DESCRIPTION, VARIABLES_HELP, clean_template


def style_label(use_embed) -> str:
    return "嵌入式訊息" if use_embed else "純文字"


def message_field(message: str) -> str:
    text = message.replace("`", "ˋ")
    return f"```{text if len(text) <= 700 else text[:699] + '…'}```"


class MessageModal(PanelModal):
    """The pop-up where the message text is typed (several lines are fine)."""

    def __init__(self, kind: str, title: str, current: str, rebuild: Callable[[], Awaitable[PanelView]]) -> None:
        super().__init__(title)
        self.kind, self.rebuild = kind, rebuild
        self.text = discord.ui.TextInput(style=discord.TextStyle.paragraph, default=current, required=True,
                                         max_length=config.MAX_MESSAGE_LENGTH, placeholder="輸入訊息內容")
        self.add_item(discord.ui.Label(text="訊息內容", description=f"最多 {config.MAX_MESSAGE_LENGTH} 個字，可以換行",
                                       component=self.text))
        self.add_item(discord.ui.TextDisplay(f"**可以使用的變數**\n`{VARIABLES_HELP}`\n{VARIABLES_DESCRIPTION}"))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = clean_template(self.text.value)
        if not text:
            await reject(interaction, "訊息內容不能是空的。")
            return
        await interaction.response.defer()
        await database.update_settings(self.kind, interaction.guild_id, message=text)
        await show(interaction, await self.rebuild())
