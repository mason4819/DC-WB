"""Rotating bot status. Everything for this feature lives in this one file.

- Edit STATUSES below to change the texts. Each item is (activity type, text).
  {servers} and {members} are replaced with live numbers (read from memory, no API calls).
- STATUS_INTERVAL_SECONDS is how often the status changes. Discord allows about
  5 presence updates per 20 seconds, so do not go below 5.
- Set STATUS_ROTATION=false in the environment to switch the feature off.
- Cost: one tiny gateway message every few seconds. No database, no HTTP requests.
"""
import itertools
import logging
import os
import random

import discord
from discord.ext import commands, tasks

import config  # noqa: F401  (importing it loads the .env file)

log = logging.getLogger(__name__)

STATUS_INTERVAL_SECONDS = 5

PLAYING = discord.ActivityType.playing
WATCHING = discord.ActivityType.watching
LISTENING = discord.ActivityType.listening

STATUSES = [
    (WATCHING,  "👀 誰剛進來了？"),
    (PLAYING,   "👋 在門口揮手"),
    (LISTENING, "🔔 叮咚～有人來了"),
    (PLAYING,   "🪑 搬椅子給新朋友"),
    (WATCHING,  "🏠 {servers} 個伺服器的大門"),
    (PLAYING,   "🎈 清點人數：{members} 位"),
    (LISTENING, "☕ 大家聊天的聲音"),
    (WATCHING,  "🌙 深夜報到的夜貓子"),
    (PLAYING,   "🧹 打掃迎賓區"),
    (PLAYING,   "💡 /歡迎 快速設定 就能開始"),
    (WATCHING,  "🍵 泡茶等你入座"),
    (LISTENING, "🎶 歡迎光臨～"),
    (PLAYING,   "🍪 發餅乾給新人"),
    (WATCHING,  "📮 收新成員的報到單"),
    (PLAYING,   "🚪 顧著大門不打瞌睡"),
]


class StatusCog(commands.Cog, name="Status"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        order = STATUSES.copy()
        random.shuffle(order)                 # different order after every restart
        self._cycle = itertools.cycle(order)

    async def cog_load(self) -> None:
        if os.getenv("STATUS_ROTATION", "true").strip().lower() in {"0", "false", "no", "off"}:
            log.info("Status rotation is turned off")
            return
        self.rotate.start()

    async def cog_unload(self) -> None:
        self.rotate.cancel()

    def _next_activity(self) -> discord.Activity:
        kind, text = next(self._cycle)
        if "{" in text:  # only count servers and members when the text needs them
            members = sum(guild.member_count or 0 for guild in self.bot.guilds)
            text = text.replace("{servers}", str(len(self.bot.guilds))).replace("{members}", str(members))
        return discord.Activity(type=kind, name=text)

    @tasks.loop(seconds=STATUS_INTERVAL_SECONDS)
    async def rotate(self) -> None:
        try:
            await self.bot.change_presence(status=discord.Status.online, activity=self._next_activity())
        except Exception:
            # A reconnect in progress can make this fail once; the next tick simply tries again.
            log.debug("Could not update the status this time", exc_info=True)

    @rotate.before_loop
    async def _wait_until_connected(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatusCog(bot))
