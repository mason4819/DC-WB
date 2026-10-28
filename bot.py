"""Entry point for the Welcome Bot.

Run with:  python bot.py
"""
import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands

import config
import database
import keepalive
from utils import remote

log = logging.getLogger("welcomebot")

EXTENSIONS = (
    "cogs.welcome",
    "cogs.leave",
    "cogs.autorole",
    "cogs.settings",
    "cogs.status",
)


class BotTree(app_commands.CommandTree):
    """Command tree with one friendly error handler for every slash command."""

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        command_name = interaction.command.qualified_name if interaction.command else "unknown"
        if isinstance(error, app_commands.MissingPermissions):
            text = "你需要「管理伺服器」或「管理員」權限才能使用這個指令。"
        elif isinstance(error, app_commands.NoPrivateMessage):
            text = "這個指令只能在伺服器內使用。"
        elif isinstance(error, app_commands.CheckFailure):
            text = "你沒有使用這個指令的權限。"
        elif isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, database.DatabaseError):
            log.error("Database error in /%s", command_name, exc_info=error.original)
            text = "目前無法讀取或儲存設定，請稍後再試。"
        else:
            log.error("Unhandled error in /%s", command_name, exc_info=error)
            text = "執行指令時發生錯誤，請稍後再試。"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass  # the interaction expired; nothing else we can do


class WelcomeBot(commands.Bot):
    def __init__(self) -> None:
        # Only the intents we need. SERVER MEMBERS is privileged and must be
        # enabled in the Developer Portal (join/leave events).
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        super().__init__(
            command_prefix=commands.when_mentioned,   # no prefix commands, slash only
            intents=intents,
            tree_cls=BotTree,
            help_command=None,
            max_messages=None,                         # no message cache
            chunk_guilds_at_startup=False,             # do not download member lists
            member_cache_flags=discord.MemberCacheFlags.none(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self._health_runner = None

    async def setup_hook(self) -> None:
        await database.init_db()
        # Health check endpoint for Render / UptimeRobot (only when PORT is set).
        self._health_runner = await keepalive.start(config.PORT)
        for extension in EXTENSIONS:
            await self.load_extension(extension)
        if config.SYNC_COMMANDS:
            try:
                synced = await self.tree.sync()
                log.info("Synced %d slash commands", len(synced))
            except discord.HTTPException:
                log.exception("Slash command sync failed; existing commands stay available")

    async def close(self) -> None:
        await keepalive.stop(self._health_runner)
        await database.close()
        await remote.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s in %d servers", self.user, len(self.guilds))


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("discord").setLevel(logging.WARNING)

    if sys.version_info < (3, 11):
        sys.exit("Python 3.11 or newer is required.")
    if not config.TOKEN:
        sys.exit("DISCORD_TOKEN is missing. Copy .env.example to .env and add your bot token.")

    discord.VoiceClient.warn_nacl = False  # voice is not used
    bot = WelcomeBot()
    try:
        bot.run(config.TOKEN, log_handler=None)  # we configured logging ourselves
    except discord.LoginFailure:
        sys.exit("Login failed: the bot token is invalid.")
    except discord.PrivilegedIntentsRequired:
        sys.exit("Enable 'SERVER MEMBERS INTENT' for your bot in the Discord Developer Portal.")


if __name__ == "__main__":
    main()
