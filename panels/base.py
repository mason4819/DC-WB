"""Shared pieces of the /設定 panel.

The panel is ONE private (ephemeral) message built with Discord's layout components: a single
box with text, and the button that changes each setting sits right next to that setting.
Every page is a LayoutView, and moving between pages edits that same message, so nothing piles
up in the chat. No emoji anywhere.
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
PRIMARY, SECONDARY = discord.ButtonStyle.primary, discord.ButtonStyle.secondary
SUCCESS, DANGER = discord.ButtonStyle.success, discord.ButtonStyle.danger


def short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def on_off(value) -> str:
    return "已啟用" if value else "已停用"


def make_button(label: str, callback: Optional[Callback] = None, *, style: discord.ButtonStyle = SECONDARY,
                disabled: bool = False, url: Optional[str] = None) -> discord.ui.Button:
    button = discord.ui.Button(label=label, style=style, disabled=disabled, url=url)
    if callback is not None:
        button.callback = callback
    return button


async def reject(interaction: discord.Interaction, text: str) -> None:
    """Send a private message (an error, usually) without touching the panel."""
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


async def handle_error(interaction: discord.Interaction, error: Exception) -> None:
    if isinstance(error, database.DatabaseError):
        log.warning("Database error in the settings panel", exc_info=error)
        text = "資料庫目前無法使用，請稍後再試。"
    else:
        log.error("Unexpected error in the settings panel", exc_info=error)
        text = "發生未預期的錯誤，已記錄下來。請重新開啟 /設定 再試一次。"
    try:
        await reject(interaction, text)
    except discord.HTTPException:
        pass


class PanelView(discord.ui.LayoutView):
    """One page of the panel. Subclasses fill the box with heading(), item(), select() and so on."""

    def __init__(self, bot, opener: discord.Interaction, *, accent: Optional[int] = None) -> None:
        super().__init__(timeout=TIMEOUT)
        self.bot = bot
        self.opener = opener            # the /設定 interaction: its response is the message every page edits
        self.reset(accent)

    def reset(self, accent: Optional[int] = None) -> None:
        """Start the page over with an empty box."""
        self.clear_items()
        self.box = discord.ui.Container(accent_colour=accent if accent is not None else config.WELCOME_COLOR)
        self.add_item(self.box)

    # ------------------------------------------------------------ page building
    def heading(self, title: str, subtitle: str = "") -> None:
        self.box.add_item(discord.ui.TextDisplay(f"## {title}" + (f"\n{subtitle}" if subtitle else "")))

    def text(self, content: str) -> None:
        self.box.add_item(discord.ui.TextDisplay(content))

    def divider(self) -> None:
        self.box.add_item(discord.ui.Separator())

    def item(self, title: str, value: str, label: str, callback: Callback, *, style: discord.ButtonStyle = SECONDARY,
             disabled: bool = False) -> None:
        """A setting: its name and current value on the left, the button that changes it on the right."""
        self.box.add_item(discord.ui.Section(f"**{title}**\n{value}",
                                             accessory=make_button(label, callback, style=style, disabled=disabled)))

    def select(self, menu: discord.ui.Select) -> None:
        self.box.add_item(discord.ui.ActionRow(menu))

    def buttons(self, *buttons: discord.ui.Button) -> None:
        self.box.add_item(discord.ui.ActionRow(*buttons))

    def notes(self, lines: list[str], title: str = "需要注意") -> None:
        if lines:
            self.text(f"**{title}**\n" + short("\n".join(f"- {line}" for line in lines), 1200))

    def footer(self) -> None:
        self.text(f"-# 歡迎機器人 {config.VERSION}")

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
        try:                                    # grey out the buttons that no longer work
            for child in self.walk_children():
                if hasattr(child, "disabled"):
                    child.disabled = True
            await self.opener.edit_original_response(view=self)
        except discord.HTTPException:
            pass

    async def go(self, interaction: discord.Interaction, page: "PanelView") -> None:
        """Replace this page with another one in the same message."""
        self.stop()
        await show(interaction, page)


async def show(interaction: discord.Interaction, page: PanelView) -> None:
    """Put a page into the panel message. Defers first so a slow database never makes it time out."""
    if not interaction.response.is_done():
        await interaction.response.defer()
    await interaction.edit_original_response(view=page)


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
        text = "你不是機器人擁有者，不能使用後臺。\n只有環境變數 OWNER_IDS 裡列出的使用者 ID 可以使用。"
    else:
        text = "後臺目前無法使用：機器人還沒有設定環境變數 OWNER_IDS（填入可以使用後臺的使用者 ID）。"
    await reject(interaction, text)
    return False
