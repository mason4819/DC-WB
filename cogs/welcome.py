"""Welcome system: on_member_join listener and /welcome commands."""
import asyncio
import logging
import sqlite3
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import database
from utils import image as imaging
from utils.formatter import VARIABLES_HELP, clean_template, render_message
from utils.messaging import (STYLE_CHOICES, AdminGroup, build_message_kwargs, channel_warnings,
                             deliver, reply)

log = logging.getLogger(__name__)

MESSAGE_HELP = f"Message text. Variables: {VARIABLES_HELP}"
MessageText = app_commands.Range[str, 1, config.MAX_MESSAGE_LENGTH]


class WelcomeCog(commands.Cog, name="Welcome"):
    welcome = AdminGroup(name="welcome", description="Configure the welcome system")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Only one image is rendered at a time, which caps CPU and RAM during join bursts.
        self._image_lock = asyncio.Semaphore(1)

    # ------------------------------------------------------------------ events
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        try:
            settings = database.get_settings("welcome", member.guild.id)
            if not settings["enabled"] or not settings["channel_id"]:
                return
            sent, notes = await self._send_welcome(member, settings)
            if not sent or notes:
                log.warning("Welcome in guild %s: sent=%s notes=%s", member.guild.id, sent, notes)
        except sqlite3.Error:
            log.exception("Database error while welcoming a member in guild %s", member.guild.id)
        except Exception:
            log.exception("Unexpected error while welcoming a member in guild %s", member.guild.id)

    # ----------------------------------------------------------------- helpers
    async def _send_welcome(self, member: discord.Member, settings: dict) -> tuple[bool, list[str]]:
        text = render_message(settings["message"], member, member.guild)
        make_image = (lambda: self._make_image(member, settings)) if settings["image_enabled"] else None
        return await deliver(
            bot=self.bot, guild=member.guild, channel_id=settings["channel_id"], user=member,
            text=text, use_embed=bool(settings["use_embed"]), colour=config.WELCOME_COLOR,
            make_image=make_image, ping_user=True,
        )

    async def _make_image(self, member: discord.Member, settings: dict) -> Optional[discord.File]:
        """Render the welcome image. Returns None on any failure instead of raising."""
        try:
            avatar_bytes = None
            try:
                avatar_bytes = await member.display_avatar.replace(size=256, format="png").read()
            except discord.DiscordException:
                log.warning("Could not download the avatar of %s", member.id)

            background = None
            if settings["background_path"]:
                candidate = config.DATA_DIR / settings["background_path"]
                background = candidate if candidate.is_file() else None

            async with self._image_lock:
                buffer = await asyncio.to_thread(
                    imaging.build_welcome_image, avatar_bytes, member.display_name,
                    member.guild.name, member.guild.member_count or 0, background,
                )
            return discord.File(buffer, filename="welcome.png")
        except Exception:
            log.exception("Welcome image generation failed in guild %s", member.guild.id)
            return None

    # --------------------------------------------------------------- commands
    @welcome.command(name="setup", description="Turn on the welcome system and choose a channel")
    @app_commands.describe(channel="Channel where welcome messages are posted", message=MESSAGE_HELP,
                           style="Plain text or embed", image="Attach a generated welcome image")
    @app_commands.choices(style=STYLE_CHOICES)
    async def welcome_setup(self, interaction: discord.Interaction, channel: discord.TextChannel,
                            message: Optional[MessageText] = None,
                            style: Optional[app_commands.Choice[str]] = None,
                            image: Optional[bool] = None) -> None:
        fields: dict = {"enabled": 1, "channel_id": channel.id}
        if message:
            fields["message"] = clean_template(message)
        if style:
            fields["use_embed"] = int(style.value == "embed")
        if image is not None:
            fields["image_enabled"] = int(image)
        database.update_settings("welcome", interaction.guild_id, **fields)

        settings = database.get_settings("welcome", interaction.guild_id)
        notes = await channel_warnings(self.bot, channel, embeds=bool(settings["use_embed"]),
                                       files=bool(settings["image_enabled"]))
        text = f"✅ Welcome messages are now enabled in {channel.mention}."
        if notes:
            text += "\n" + "\n".join(f"⚠️ {note}" for note in notes)
        text += "\nTry it with `/welcome test` or `/welcome preview`."
        await reply(interaction, text)

    @welcome.command(name="enable", description="Enable the welcome system")
    async def welcome_enable(self, interaction: discord.Interaction) -> None:
        settings = database.get_settings("welcome", interaction.guild_id)
        database.update_settings("welcome", interaction.guild_id, enabled=1)
        if not settings["channel_id"]:
            await reply(interaction, "✅ Enabled, but no channel is set yet. Use `/welcome channel` to pick one.")
        else:
            await reply(interaction, "✅ The welcome system is enabled.")

    @welcome.command(name="disable", description="Disable the welcome system")
    async def welcome_disable(self, interaction: discord.Interaction) -> None:
        database.update_settings("welcome", interaction.guild_id, enabled=0)
        await reply(interaction, "✅ The welcome system is disabled. Your settings are kept.")

    @welcome.command(name="channel", description="Choose the welcome channel")
    @app_commands.describe(channel="Channel where welcome messages are posted")
    async def welcome_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        database.update_settings("welcome", interaction.guild_id, channel_id=channel.id)
        settings = database.get_settings("welcome", interaction.guild_id)
        notes = await channel_warnings(self.bot, channel, embeds=bool(settings["use_embed"]),
                                       files=bool(settings["image_enabled"]))
        text = f"✅ Welcome messages will be posted in {channel.mention}."
        if notes:
            text += "\n" + "\n".join(f"⚠️ {note}" for note in notes)
        await reply(interaction, text)

    @welcome.command(name="message", description="Set the welcome message text and style")
    @app_commands.describe(text=MESSAGE_HELP, style="Plain text or embed")
    @app_commands.choices(style=STYLE_CHOICES)
    async def welcome_message(self, interaction: discord.Interaction, text: Optional[MessageText] = None,
                              style: Optional[app_commands.Choice[str]] = None) -> None:
        if text is None and style is None:
            settings = database.get_settings("welcome", interaction.guild_id)
            kind = "embed" if settings["use_embed"] else "plain text"
            await reply(interaction, f"Current style: **{kind}**\nCurrent message:\n```{settings['message']}```\n"
                                     f"Variables: `{VARIABLES_HELP}`\nUse `\\n` for a line break.")
            return
        fields: dict = {}
        if text is not None:
            fields["message"] = clean_template(text)
        if style is not None:
            fields["use_embed"] = int(style.value == "embed")
        database.update_settings("welcome", interaction.guild_id, **fields)
        await reply(interaction, "✅ Welcome message updated. Check it with `/welcome preview`.")

    @welcome.command(name="image", description="Configure the welcome image")
    @app_commands.describe(enabled="Turn the welcome image on or off",
                           background="Custom background (PNG or JPG, up to 5 MB)",
                           reset="Restore the default background")
    async def welcome_image(self, interaction: discord.Interaction, enabled: Optional[bool] = None,
                            background: Optional[discord.Attachment] = None,
                            reset: Optional[bool] = None) -> None:
        guild_id = interaction.guild_id
        settings = database.get_settings("welcome", guild_id)

        if enabled is None and background is None and not reset:
            await reply(interaction,
                        f"Welcome image: **{'on' if settings['image_enabled'] else 'off'}**\n"
                        f"Background: **{'custom' if settings['background_path'] else 'default'}**")
            return

        await interaction.response.defer(ephemeral=True)
        fields: dict = {}
        destination = config.CUSTOM_BACKGROUND_DIR / f"{guild_id}.jpg"

        if reset:
            fields["background_path"] = None
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                log.warning("Could not delete %s", destination)

        if background is not None:
            content_type = (background.content_type or "").lower()
            looks_valid = content_type in {"image/png", "image/jpeg"} or \
                background.filename.lower().endswith((".png", ".jpg", ".jpeg"))
            if not looks_valid:
                await reply(interaction, "❌ Please upload a PNG or JPG image.")
                return
            if background.size > config.MAX_BACKGROUND_BYTES:
                await reply(interaction, "❌ That image is larger than 5 MB.")
                return
            try:
                data = await background.read()
                async with self._image_lock:
                    await asyncio.to_thread(imaging.save_custom_background, data, destination)
            except imaging.BackgroundError as exc:
                await reply(interaction, f"❌ {exc}")
                return
            except discord.HTTPException:
                await reply(interaction, "❌ I could not download that attachment. Please try again.")
                return
            fields["background_path"] = f"backgrounds/{guild_id}.jpg"

        if enabled is not None:
            fields["image_enabled"] = int(enabled)

        database.update_settings("welcome", guild_id, **fields)
        await reply(interaction, "✅ Welcome image settings updated. Check them with `/welcome preview`.")

    @welcome.command(name="test", description="Send a test welcome message to the welcome channel")
    async def welcome_test(self, interaction: discord.Interaction) -> None:
        settings = database.get_settings("welcome", interaction.guild_id)
        if not settings["channel_id"]:
            await reply(interaction, "❌ No welcome channel is set. Use `/welcome channel` first.")
            return
        await interaction.response.defer(ephemeral=True)
        sent, notes = await self._send_welcome(interaction.user, settings)
        if sent:
            text = f"✅ Test message sent to <#{settings['channel_id']}>."
        else:
            text = "❌ The test message could not be sent."
        if notes:
            text += "\n" + "\n".join(f"⚠️ {note}" for note in notes)
        await reply(interaction, text)

    @welcome.command(name="preview", description="Preview the welcome message here, visible only to you")
    async def welcome_preview(self, interaction: discord.Interaction) -> None:
        settings = database.get_settings("welcome", interaction.guild_id)
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        text = render_message(settings["message"], member, interaction.guild)
        file = await self._make_image(member, settings) if settings["image_enabled"] else None
        kwargs = build_message_kwargs(member, text, bool(settings["use_embed"]), config.WELCOME_COLOR, file)
        await interaction.followup.send(ephemeral=True, **kwargs)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))
