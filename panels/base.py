"""Shared pieces of the /設定 panel.

The panel is ONE private (ephemeral) message. Every page is a View plus an embed, and moving
between pages edits that same message, so nothing piles up in the chat.
"""
import logging
from typing import Awaitable, Callable, Optional

import discord

import config
import database
from utils.access import AccessCheckFailed, is_authorized, is_bot_owner

log = logging.getLogger(__name__)

TIMEOUT = 840            # an interaction token lives 15 minutes, so stop a little before that
MODAL_TIMEOUT = 600
Callback = Callable[[discord.Interaction], Awaitable[None]]


def short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def make_embed(title: str, description: str = "", *, colour: Optional[int] = None) -> discord.Embed:
    embed = discord.Embed(title=title, description=description or None, colour=colour or config.WELCOME_COLOR)
    embed.set_footer(text=f"歡迎機器人 {config.VERSION}")
    return embed


async def reject(interaction: discord.Interaction, text: str) -> None:
    """Send a private message (an error, usually) without touching the panel."""
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


async def handle_error(interaction: discord.Interaction, error: Exception) -> None:
    if isinstance(error, database.DatabaseError):
        log.warning("Database error in the settings panel", exc_info=error)
        text = "❌ 資料庫目前無法使用，請稍後再試。"
    else:
        log.error("Unexpected error in the settings panel", exc_info=error)
        text = "❌ 發生未預期的錯誤，已記錄下來。請重新開啟 /設定 再試一次。"
    try:
        await reject(interaction, text)
    except discord.HTTPException:
        pass


class PanelView(discord.ui.View):
    """One page of the panel. Subclasses fill in self.embed and add their buttons and menus."""

    def __init__(self, bot, opener: discord.Interaction) -> None:
        super().__init__(timeout=TIMEOUT)
        self.bot = bot
        self.opener = opener            # the /設定 interaction: its response is the message every page edits
        self.embed = make_embed("")

    # ----------------------------------------------------------------- checks
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opener.user.id:
            await reject(interaction, "這個面板不是你開的，請自己輸入 /設定。")
            return False
        try:
            if await is_authorized(interaction):
                return True
        except AccessCheckFailed:
            await reject(interaction, "目前無法確認你的權限，請稍後再試。")
            return False
        await reject(interaction, "你已經沒有使用設定面板的權限了。")
        return False

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await handle_error(interaction, error)

    async def on_timeout(self) -> None:
        try:
            await self.opener.edit_original_response(view=None)      # remove the buttons that no longer work
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------ small helpers
    def add_button(self, label: str, callback: Callback, *, style: discord.ButtonStyle = discord.ButtonStyle.secondary,
                   row: Optional[int] = None, emoji: Optional[str] = None, disabled: bool = False) -> discord.ui.Button:
        button = discord.ui.Button(label=label, style=style, row=row, emoji=emoji, disabled=disabled)
        button.callback = callback
        self.add_item(button)
        return button

    async def go(self, interaction: discord.Interaction, page: "PanelView") -> None:
        """Replace this page with another one in the same message."""
        self.stop()
        await show(interaction, page)


async def show(interaction: discord.Interaction, page: PanelView) -> None:
    """Put a page into the panel message. Defers first so a slow database never makes it time out."""
    if not interaction.response.is_done():
        await interaction.response.defer()
    await interaction.edit_original_response(embed=page.embed, view=page)


async def go_home(view: PanelView, interaction: discord.Interaction) -> None:
    from panels.home import HomeView             # imported here because home imports the other pages
    await view.go(interaction, await HomeView.create(view.bot, view.opener))


class PanelModal(discord.ui.Modal):
    """Base class for the pop-up forms (the Discord 'submit' panels)."""

    def __init__(self, title: str) -> None:
        super().__init__(title=title, timeout=MODAL_TIMEOUT)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await handle_error(interaction, error)


async def require_bot_owner(interaction: discord.Interaction) -> bool:
    """For the back office: only the user IDs in OWNER_IDS. Everyone else gets an error."""
    if is_bot_owner(interaction.user.id):
        return True
    if config.OWNER_IDS:
        text = "❌ 你不是機器人擁有者，不能使用後臺。\n（只有環境變數 `OWNER_IDS` 裡列出的使用者 ID 可以使用。）"
    else:
        text = "❌ 後臺目前無法使用：機器人還沒有設定環境變數 `OWNER_IDS`（填入可以使用後臺的使用者 ID）。"
    await reject(interaction, text)
    return False
