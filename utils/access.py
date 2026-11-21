"""Who may open the /設定 panel, and who may use the back office inside it.

Rule for the /設定 panel (and every page in it):
  - The server owner and members with Administrator always may.
  - If the server has NOT set up a management list, members with Manage Server may too.
  - If the server HAS a management list (/管理員), only the owner, Administrators, listed
    members and members holding a listed role may. Manage Server alone is then not enough.
Editing that list (the 權限名單 page) is limited to the owner and Administrators, so a listed
member cannot add more people.

The back office page is a separate rule: only the user IDs in the OWNER_IDS environment variable.
"""
import discord
from discord import app_commands

import config
import database


class NotAuthorized(app_commands.CheckFailure):
    """The user may not use this command."""


class NotOwnerOrAdmin(NotAuthorized):
    """The command is reserved for the owner and Administrators."""


class AccessCheckFailed(app_commands.CheckFailure):
    """The permission list could not be read (for example the database is unreachable)."""


def is_bot_owner(user_id: int) -> bool:
    """True only for the user IDs listed in OWNER_IDS (nobody if it is empty)."""
    return user_id in config.OWNER_IDS


def is_owner_or_admin(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    return guild is not None and (interaction.user.id == guild.owner_id
                                  or interaction.permissions.administrator)


async def is_authorized(interaction: discord.Interaction) -> bool:
    """True if the user may use the setup commands in this server."""
    if is_owner_or_admin(interaction):
        return True
    guild = interaction.guild
    if guild is None:
        return False
    try:
        access = await database.get_access(guild.id)
    except database.DatabaseError as exc:
        raise AccessCheckFailed() from exc
    if not access["user"] and not access["role"]:
        return interaction.permissions.manage_guild
    if interaction.user.id in access["user"]:
        return True
    role_ids = {role.id for role in getattr(interaction.user, "roles", [])}
    return bool(role_ids.intersection(access["role"]))


async def require_authorized(interaction: discord.Interaction) -> bool:
    """app_commands check: allow, or raise a friendly error."""
    if await is_authorized(interaction):
        return True
    raise NotAuthorized()
