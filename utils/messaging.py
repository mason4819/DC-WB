"""Shared Discord helpers: permission checks, safe message delivery, command group."""
import logging
from typing import Awaitable, Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.access import NotOwnerOrAdmin, is_owner_or_admin, require_authorized

log = logging.getLogger(__name__)

STYLE_CHOICES = [
    app_commands.Choice(name="嵌入式訊息 (Embed)", value="embed"),
    app_commands.Choice(name="純文字", value="text"),
]


class AdminGroup(app_commands.Group):
    """Setup command group. Who may use it is decided by utils/access.py.

    The group is visible to everyone in Discord's command list and the bot checks the
    rule itself, so members added to the management list can use it without needing
    the Manage Server permission. (Server owners can still hide or limit a command in
    Server Settings > Integrations.)
    """

    def __init__(self, *, name: str, description: str) -> None:
        super().__init__(name=name, description=description, guild_only=True)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await require_authorized(interaction)


class OwnerGroup(app_commands.Group):
    """Group reserved for the server owner and Administrators (editing the management list)."""

    def __init__(self, *, name: str, description: str) -> None:
        super().__init__(name=name, description=description, guild_only=True,
                         default_permissions=discord.Permissions(administrator=True))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_owner_or_admin(interaction):
            return True
        raise NotOwnerOrAdmin()


async def reply(interaction: discord.Interaction, text: str) -> None:
    """Send an ephemeral reply, whether or not the interaction was already deferred."""
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


async def get_bot_member(bot: commands.Bot, guild: discord.Guild) -> Optional[discord.Member]:
    """Return the bot's own member. The member cache is disabled, so fall back to the API."""
    if guild.me is not None:
        return guild.me
    try:
        return await guild.fetch_member(bot.user.id)
    except discord.HTTPException:
        log.warning("Could not fetch the bot member in guild %s", guild.id)
        return None


def role_problem(me: discord.Member, role: discord.Role) -> Optional[str]:
    """Explain why the bot cannot hand out a role, or return None if it can."""
    if role.is_default():
        return "@everyone 不能當作自動身分組。"
    if role.managed:
        return f"{role.mention} 是由整合服務管理的身分組，無法手動發放。"
    if not me.guild_permissions.manage_roles:
        return "機器人缺少「管理身分組」權限。"
    if role >= me.top_role:
        return (f"{role.mention} 的位置高於或等於機器人的最高身分組，"
                "請到「伺服器設定 > 身分組」把機器人的身分組拖到它上面。")
    return None


async def channel_warnings(bot: commands.Bot, channel: Optional[discord.abc.GuildChannel], *,
                           embeds: bool, files: bool) -> list[str]:
    """List permission problems that would stop messages from arriving in a channel."""
    if not isinstance(channel, discord.TextChannel):
        return ["設定的頻道已經不存在，請用「頻道」指令重新選擇。"]
    me = await get_bot_member(bot, channel.guild)
    if me is None:
        return ["無法確認機器人在這個伺服器的權限。"]
    perms = channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages):
        return [f"機器人無法在 {channel.mention} 發言，請給予「檢視頻道」與「發送訊息」權限。"]
    notes = []
    if embeds and not perms.embed_links:
        notes.append(f"機器人在 {channel.mention} 缺少「嵌入連結」權限，將改用純文字發送。")
    if files and not perms.attach_files:
        notes.append(f"機器人在 {channel.mention} 缺少「附加檔案」權限，將略過歡迎圖片。")
    return notes


def build_message_kwargs(user: discord.abc.User, text: str, use_embed: bool, colour: int,
                         file: Optional[discord.File]) -> dict:
    """Build the keyword arguments for channel.send() / followup.send()."""
    kwargs: dict = {}
    if use_embed:
        embed = discord.Embed(description=text, colour=colour)
        if file is not None:
            embed.set_image(url=f"attachment://{file.filename}")
        else:
            embed.set_thumbnail(url=user.display_avatar.url)
        kwargs["embed"] = embed
    else:
        kwargs["content"] = text
    if file is not None:
        kwargs["file"] = file
    return kwargs


async def deliver(*, bot: commands.Bot, guild: discord.Guild, channel_id: Optional[int],
                  user: discord.abc.User, text: str, use_embed: bool, colour: int,
                  make_image: Optional[Callable[[], Awaitable[Optional[discord.File]]]] = None,
                  ping_user: bool = False) -> tuple[bool, list[str]]:
    """Post a welcome/leave message and never raise.

    Returns (sent, notes). Notes describe problems or fallbacks in plain English.
    """
    channel = guild.get_channel(channel_id) if channel_id else None
    if not isinstance(channel, discord.TextChannel):
        return False, ["設定的頻道已經不存在，請用「頻道」指令重新選擇。"]

    me = await get_bot_member(bot, guild)
    if me is None:
        return False, ["無法確認機器人在這個伺服器的權限。"]

    perms = channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages):
        return False, [f"機器人無法在 {channel.mention} 發言，請給予「檢視頻道」與「發送訊息」權限。"]

    notes: list[str] = []
    if use_embed and not perms.embed_links:
        use_embed = False
        notes.append(f"機器人在 {channel.mention} 缺少「嵌入連結」權限，已改用純文字發送。")

    file: Optional[discord.File] = None
    if make_image is not None:
        if not perms.attach_files:
            notes.append(f"機器人在 {channel.mention} 缺少「附加檔案」權限，已略過歡迎圖片。")
        else:
            file = await make_image()
            if file is None:
                notes.append("歡迎圖片產生失敗，已略過圖片。")

    kwargs = build_message_kwargs(user, text, use_embed, colour, file)
    kwargs["allowed_mentions"] = (discord.AllowedMentions(users=[user], roles=False, everyone=False)
                                  if ping_user else discord.AllowedMentions.none())
    try:
        await channel.send(**kwargs)
    except discord.Forbidden:
        return False, [f"Discord 拒絕機器人在 {channel.mention} 發言，請檢查頻道權限。"]
    except discord.NotFound:
        return False, ["設定的頻道已被刪除，請用「頻道」指令重新選擇。"]
    except discord.HTTPException as exc:
        log.warning("Discord API error while posting in guild %s: %s", guild.id, exc)
        return False, [f"Discord 回傳錯誤（{exc.status}），請稍後再試。"]
    return True, notes
