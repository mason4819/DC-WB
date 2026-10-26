"""Shared Discord helpers: permission checks, safe message delivery, command group."""
import logging
from typing import Awaitable, Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

STYLE_CHOICES = [
    app_commands.Choice(name="Embed", value="embed"),
    app_commands.Choice(name="Plain text", value="text"),
]


class AdminGroup(app_commands.Group):
    """Slash command group limited to Administrator or Manage Server.

    default_permissions hides the commands from regular members in Discord's UI.
    interaction_check enforces the rule again on the bot side, because server
    owners can override command permissions in Integrations settings.
    """

    def __init__(self, *, name: str, description: str) -> None:
        super().__init__(
            name=name,
            description=description,
            default_permissions=discord.Permissions(manage_guild=True),
            guild_only=True,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        perms = interaction.permissions
        if perms.administrator or perms.manage_guild:
            return True
        raise app_commands.MissingPermissions(["manage_guild"])


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
        return "@everyone cannot be used as an auto role."
    if role.managed:
        return f"{role.mention} is managed by an integration and cannot be assigned manually."
    if not me.guild_permissions.manage_roles:
        return "I am missing the **Manage Roles** permission."
    if role >= me.top_role:
        return (f"{role.mention} is higher than or equal to my highest role. "
                "Move my role above it in Server Settings > Roles.")
    return None


async def channel_warnings(bot: commands.Bot, channel: Optional[discord.abc.GuildChannel], *,
                           embeds: bool, files: bool) -> list[str]:
    """List permission problems that would stop messages from arriving in a channel."""
    if not isinstance(channel, discord.TextChannel):
        return ["The configured channel no longer exists. Choose a new one with the channel command."]
    me = await get_bot_member(bot, channel.guild)
    if me is None:
        return ["I could not check my permissions in this server."]
    perms = channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages):
        return [f"I cannot post in {channel.mention}. Grant me **View Channel** and **Send Messages** there."]
    notes = []
    if embeds and not perms.embed_links:
        notes.append(f"I am missing **Embed Links** in {channel.mention}; plain text will be used instead.")
    if files and not perms.attach_files:
        notes.append(f"I am missing **Attach Files** in {channel.mention}; the image will be skipped.")
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
        return False, ["The configured channel no longer exists. Choose a new one with the channel command."]

    me = await get_bot_member(bot, guild)
    if me is None:
        return False, ["I could not check my permissions in this server."]

    perms = channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages):
        return False, [f"I cannot post in {channel.mention}. Grant me **View Channel** and **Send Messages** there."]

    notes: list[str] = []
    if use_embed and not perms.embed_links:
        use_embed = False
        notes.append(f"I am missing **Embed Links** in {channel.mention}, so I sent plain text instead.")

    file: Optional[discord.File] = None
    if make_image is not None:
        if not perms.attach_files:
            notes.append(f"I am missing **Attach Files** in {channel.mention}, so the image was skipped.")
        else:
            file = await make_image()
            if file is None:
                notes.append("The welcome image could not be generated, so it was skipped.")

    kwargs = build_message_kwargs(user, text, use_embed, colour, file)
    kwargs["allowed_mentions"] = (discord.AllowedMentions(users=[user], roles=False, everyone=False)
                                  if ping_user else discord.AllowedMentions.none())
    try:
        await channel.send(**kwargs)
    except discord.Forbidden:
        return False, [f"Discord refused to let me post in {channel.mention}. Check my channel permissions."]
    except discord.NotFound:
        return False, ["The configured channel was deleted. Choose a new one with the channel command."]
    except discord.HTTPException as exc:
        log.warning("Discord API error while posting in guild %s: %s", guild.id, exc)
        return False, [f"Discord returned an error ({exc.status}). Please try again later."]
    return True, notes
