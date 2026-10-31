"""Entry point for the Welcome Bot.

Run with:  python bot.py

With Turso configured, run it on two hosts: only one connects to Discord at a time
(see ha.py). Without Turso it simply runs as a single bot.
"""
import asyncio
import logging
import signal
import sys
import uuid
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import busywork
import database
import ha
import webapp
from utils import remote
from utils.access import AccessCheckFailed, NotAuthorized, NotBotOwner, NotOwnerOrAdmin

log = logging.getLogger("welcomebot")

EXTENSIONS = (
    "cogs.welcome",
    "cogs.leave",
    "cogs.autorole",
    "cogs.settings",
    "cogs.access",
    "cogs.backend",
    "cogs.status",
)


class BotTree(app_commands.CommandTree):
    """Command tree with one friendly error handler for every slash command."""

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        command_name = interaction.command.qualified_name if interaction.command else "unknown"
        if isinstance(error, NotBotOwner):
            text = "只有機器人的擁有者可以使用後臺指令。"
        elif isinstance(error, NotOwnerOrAdmin):
            text = "這個指令只有伺服器擁有者或管理員（Administrator）可以使用。"
        elif isinstance(error, NotAuthorized):
            text = ("你沒有權限使用這個指令。\n只有伺服器擁有者、管理員，以及被加入管理名單的成員或身分組可以使用"
                    "（如果伺服器沒有設定名單，則需要「管理伺服器」權限）。")
        elif isinstance(error, AccessCheckFailed):
            log.error("Could not read the access list", exc_info=error.__cause__)
            text = "目前無法確認你的權限，請稍後再試。"
        elif isinstance(error, app_commands.MissingPermissions):
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
            owner_ids=set(config.OWNER_IDS) or None,   # empty = use the application's owner
        )

    async def run_until_closed(self) -> None:
        """Connect to Discord and stay connected until close() is called."""
        await self.start(config.TOKEN)

    async def setup_hook(self) -> None:
        for extension in EXTENSIONS:
            await self.load_extension(extension)
        if config.SYNC_COMMANDS:
            try:
                synced = await self.tree.sync()
                log.info("Synced %d slash commands", len(synced))
                if config.ADMIN_GUILD_ID:
                    guild_synced = await self.tree.sync(guild=discord.Object(id=config.ADMIN_GUILD_ID))
                    log.info("Synced %d back office commands to the admin server", len(guild_synced))
            except discord.HTTPException:
                log.exception("Slash command sync failed; existing commands stay available")

    async def on_ready(self) -> None:
        log.info("Logged in as %s in %d servers", self.user, len(self.guilds))


FATAL_ERRORS = (discord.LoginFailure, discord.PrivilegedIntentsRequired)


def _fatal_message(error: BaseException) -> str:
    if isinstance(error, discord.PrivilegedIntentsRequired):
        return "Enable 'SERVER MEMBERS INTENT' for your bot in the Discord Developer Portal."
    return "Login failed: the bot token is invalid."


async def _init_database(stop: asyncio.Event) -> None:
    """Create the tables. With a remote database, keep retrying instead of crashing."""
    while not stop.is_set():
        try:
            await database.init_db()
            return
        except database.DatabaseError as exc:
            if not config.HA_ENABLED:
                raise
            log.error("Database not ready (%s). Retrying in 15 seconds.", exc)
            try:
                await asyncio.wait_for(stop.wait(), 15)
            except asyncio.TimeoutError:
                pass


async def amain() -> Optional[str]:
    """Run everything. Returns an error message to print, or None."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass  # not available on Windows; Ctrl+C still raises KeyboardInterrupt

    supervisor: Optional[ha.Supervisor] = None
    runner = await webapp.start(config.PORT, lambda: supervisor.role if supervisor else "standalone")
    helpers: list[asyncio.Task] = []
    message: Optional[str] = None
    try:
        await _init_database(stop)
        if stop.is_set():
            return None
        helpers = [asyncio.create_task(ha.heartbeat(stop)), asyncio.create_task(busywork.run(stop))]

        if config.HA_ENABLED:
            holder = f"{config.NODE_NAME}-{uuid.uuid4().hex[:6]}"
            log.info("Failover mode on as %s", holder)
            supervisor = ha.Supervisor(holder, WelcomeBot, database.try_acquire_lease,
                                       database.release_lease, fatal=FATAL_ERRORS,
                                       on_activate=database.clear_cache)
            ha.active_supervisor = supervisor
            await supervisor.run(stop)
            if supervisor.fatal_error is not None:
                message = _fatal_message(supervisor.fatal_error)
        else:
            bot = WelcomeBot()
            bot_task = asyncio.create_task(bot.run_until_closed())
            stop_task = asyncio.create_task(stop.wait())
            await asyncio.wait({bot_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            await bot.close()
            if bot_task.done() and not bot_task.cancelled() and bot_task.exception() is not None:
                error = bot_task.exception()
                if isinstance(error, FATAL_ERRORS):
                    message = _fatal_message(error)
                else:
                    raise error
            stop_task.cancel()
    finally:
        stop.set()
        for task in helpers:
            task.cancel()
        await asyncio.gather(*helpers, return_exceptions=True)
        await webapp.stop(runner)
        await remote.close()
        await database.close()
    return message


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
    try:
        message = asyncio.run(amain())
    except KeyboardInterrupt:
        return
    if message:
        sys.exit(message)


if __name__ == "__main__":
    main()
