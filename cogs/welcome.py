"""Welcome system: the on_member_join listener and the code that builds and sends the welcome.

Everything is configured in the /設定 panel (panels/welcome.py), which calls send_welcome() and
render_image() below for its preview and test buttons."""
import asyncio
import io
import logging
from typing import Optional

import discord
from discord.ext import commands

import config
import database
from utils import image as imaging
from utils import assets_store, remote
import busywork
from utils.formatter import render_message
from utils.messaging import deliver, deliver_dm

log = logging.getLogger(__name__)

class WelcomeCog(commands.Cog, name="Welcome"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ events
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        try:
            settings = await database.get_settings("welcome", member.guild.id)
            if not settings["enabled"]:
                return
            results = await self.send_welcome(member, settings)
            if not results:                       # nothing to do, e.g. channel mode without a channel
                return
            for target, (sent, notes) in results.items():
                if not sent or notes:
                    log.warning("Welcome (%s) in guild %s: sent=%s notes=%s", target, member.guild.id, sent, notes)
        except database.DatabaseError:
            log.exception("Database error while welcoming a member in guild %s", member.guild.id)
        except Exception:
            log.exception("Unexpected error while welcoming a member in guild %s", member.guild.id)

    # ----------------------------------------------------------------- helpers
    async def send_welcome(self, member: discord.Member, settings: dict) -> dict[str, tuple[bool, list[str]]]:
        """Welcome a member the way the server chose: channel, private message, or both.

        Returns {"channel": (sent, notes), "dm": (sent, notes)} for the targets that were tried.
        The image is drawn once and reused when both are used.
        """
        mode = settings["delivery"]
        use_channel = mode in ("channel", "both") and bool(settings["channel_id"])
        use_dm = mode in ("dm", "both") and not member.bot
        text = render_message(settings["message"], member, member.guild)
        colour = imaging.theme_embed_colour(settings["theme"])
        use_embed = bool(settings["use_embed"])

        cache: dict = {}

        async def make_image() -> Optional[bytes]:
            if "png" not in cache:
                cache["png"] = await self.render_image(member, settings)
            return cache["png"]

        image_factory = make_image if settings["image_enabled"] else None
        results: dict[str, tuple[bool, list[str]]] = {}
        if use_channel:
            results["channel"] = await deliver(
                bot=self.bot, guild=member.guild, channel_id=settings["channel_id"], user=member,
                text=text, use_embed=use_embed, colour=colour, make_image=image_factory, ping_user=True)
        if use_dm:
            results["dm"] = await deliver_dm(user=member, guild=member.guild, text=text, use_embed=use_embed,
                                             colour=colour, make_image=image_factory)
        return results

    async def render_image(self, member: discord.Member, settings: dict) -> Optional[bytes]:
        """Render the welcome image as PNG bytes. Returns None on any failure instead of raising."""
        try:
            avatar_bytes = None
            try:
                avatar_bytes = await member.display_avatar.replace(size=256, format="png").read()
            except discord.DiscordException:
                log.warning("Could not download the avatar of %s", member.id)

            background, banner = None, False
            if settings["background_path"] == "builtin":          # the banner that ships with the bot
                background, banner = config.DEFAULT_BACKGROUND, True
            elif (settings["background_path"] or "").startswith("db:"):   # uploaded by the admins, kept in the database
                try:
                    background = await assets_store.background_file(member.guild.id, int(settings["background_path"][3:]))
                except (ValueError, database.DatabaseError):
                    log.warning("Could not load the stored background of guild %s", member.guild.id)
            elif settings["background_path"]:                      # old layout: a file on this host's disk
                candidate = config.DATA_DIR / settings["background_path"]
                background = candidate if candidate.is_file() else None
            theme = settings["theme"]

            buffer = None
            if remote.is_enabled():  # offload the drawing to the image worker if one is configured
                png = await remote.render(
                    avatar_bytes, member.display_name, member.guild.name, member.guild.member_count or 0,
                    background.read_bytes() if background and not banner else None, member.name,
                    banner=banner, theme=theme)
                if png is not None:
                    buffer = io.BytesIO(png)

            if buffer is None:  # no worker, or it failed: draw locally, one image at a time
                async with imaging.RENDER_LOCK:  # one image at a time, shared with the other host's requests
                    with busywork.busy():
                        buffer = await asyncio.to_thread(
                            imaging.build_welcome_image, avatar_bytes, member.display_name,
                            member.guild.name, member.guild.member_count or 0, background, member.name, theme,
                        )
            return buffer.getvalue()
        except Exception:
            log.exception("Welcome image generation failed in guild %s", member.guild.id)
            return None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))
