"""Auto role: gives new members a role and tells admins when that fails."""
import logging
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands

import database
from utils.messaging import AdminGroup, get_bot_member, reply, role_problem

log = logging.getLogger(__name__)

NOTICE_COOLDOWN = 6 * 60 * 60  # post at most one problem notice per server every 6 hours


class AutoRoleCog(commands.Cog, name="AutoRole"):
    autorole = AdminGroup(name="autorole", description="Configure the automatic role for new members")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_notice: dict[int, float] = {}  # guild_id -> monotonic time, tiny

    # ------------------------------------------------------------------ events
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        try:
            settings = database.get_settings("autorole", guild.id)
            if not settings["enabled"] or not settings["role_id"]:
                return

            role = guild.get_role(settings["role_id"])
            if role is None:
                await self._notify(guild, "The configured auto role no longer exists. "
                                          "Choose a new one with `/autorole set`.")
                return

            me = await get_bot_member(self.bot, guild)
            problem = role_problem(me, role) if me else "I could not check my own permissions."
            if problem:
                await self._notify(guild, f"I could not give {role.mention} to {member.mention}. {problem}")
                return

            try:
                await member.add_roles(role, reason="Auto role on join")
            except discord.Forbidden:
                await self._notify(guild, f"Discord refused to let me give {role.mention}. "
                                          "Check my **Manage Roles** permission and role order.")
            except discord.NotFound:
                log.info("Member %s left before the auto role was applied", member.id)
            except discord.HTTPException:
                log.warning("Discord API error while giving the auto role in guild %s", guild.id, exc_info=True)
        except sqlite3.Error:
            log.exception("Database error in auto role for guild %s", guild.id)
        except Exception:
            log.exception("Unexpected auto role error in guild %s", guild.id)

    async def _notify(self, guild: discord.Guild, text: str) -> None:
        """Log the problem and post a short notice for admins (rate limited)."""
        log.warning("Auto role problem in guild %s: %s", guild.id, text)
        now = time.monotonic()
        if now - self._last_notice.get(guild.id, float("-inf")) < NOTICE_COOLDOWN:
            return
        self._last_notice[guild.id] = now

        channel = None
        try:
            welcome = database.get_settings("welcome", guild.id)
            if welcome["channel_id"]:
                channel = guild.get_channel(welcome["channel_id"])
        except sqlite3.Error:
            pass
        if not isinstance(channel, discord.TextChannel):
            channel = guild.system_channel
        if channel is None:
            return
        try:
            await channel.send(f"⚠️ **Auto role problem:** {text}\n"
                               "*(Only admins need to act on this. It is shown at most once every 6 hours.)*",
                               allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.info("Could not post the auto role notice in guild %s", guild.id)

    # --------------------------------------------------------------- commands
    @autorole.command(name="set", description="Choose the role new members receive")
    @app_commands.describe(role="Role to give to every new member")
    async def autorole_set(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if role.is_default() or role.managed:
            await reply(interaction, "❌ That role cannot be assigned by a bot. Please pick a regular role.")
            return
        database.update_settings("autorole", interaction.guild_id, role_id=role.id, enabled=1)
        me = await get_bot_member(self.bot, interaction.guild)
        problem = role_problem(me, role) if me else None
        text = f"✅ New members will receive {role.mention}."
        if problem:
            text += f"\n⚠️ It will not work yet: {problem}"
        await reply(interaction, text)

    @autorole.command(name="remove", description="Remove the auto role setting")
    async def autorole_remove(self, interaction: discord.Interaction) -> None:
        database.update_settings("autorole", interaction.guild_id, role_id=None, enabled=0)
        await reply(interaction, "✅ The auto role was removed. New members will not receive a role.")

    @autorole.command(name="enable", description="Enable the auto role")
    async def autorole_enable(self, interaction: discord.Interaction) -> None:
        settings = database.get_settings("autorole", interaction.guild_id)
        role = interaction.guild.get_role(settings["role_id"]) if settings["role_id"] else None
        if role is None:
            await reply(interaction, "❌ No auto role is set. Use `/autorole set` first.")
            return
        database.update_settings("autorole", interaction.guild_id, enabled=1)
        me = await get_bot_member(self.bot, interaction.guild)
        problem = role_problem(me, role) if me else None
        text = f"✅ The auto role is enabled ({role.mention})."
        if problem:
            text += f"\n⚠️ It will not work yet: {problem}"
        await reply(interaction, text)

    @autorole.command(name="disable", description="Disable the auto role")
    async def autorole_disable(self, interaction: discord.Interaction) -> None:
        database.update_settings("autorole", interaction.guild_id, enabled=0)
        await reply(interaction, "✅ The auto role is disabled. The selected role is kept.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoRoleCog(bot))
