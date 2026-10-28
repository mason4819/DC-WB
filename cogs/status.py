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
    (PLAYING,   "0.1 顆 CPU 苟活中"),
    (PLAYING,   "512MB 還能撐多久"),
    (WATCHING,  "RAM 又少了"),
    (LISTENING, "CPU 在哀嚎"),
    (PLAYING,   "龜速運行中"),
    (WATCHING,  "免費主機真香"),
    (PLAYING,   "今天也在正常運作"),
    (LISTENING, "誰能捐我一點 RAM"),
    (WATCHING,  "CPU 快往生了"),
    (PLAYING,   "別催，跑不動"),
    (WATCHING,  "人太多會炸"),
    (PLAYING,   "{members} 個人擠 512MB"),
    (LISTENING, "沒有風扇也能過熱"),
    (PLAYING,   "窮鬼主機"),
    (WATCHING,  "0.1 CPU 思考中"),
    (WATCHING,  "今天應該不會炸吧"),
    (PLAYING,   "活著就好"),
    (LISTENING, "RAM 借我一下"),
    (WATCHING,  "別再開功能了"),
    (PLAYING,   "免費的就別要求太多"),
    (WATCHING,  "又是哪個功能在吃 RAM"),
    (PLAYING,   "我真的盡力了"),
    (LISTENING, "CPU：我想下班"),
    (PLAYING,   "低配也要有尊嚴"),
    (WATCHING,  "重啟就能解決的問題"),
    (PLAYING,   "今天也沒被 OOM 殺掉"),
    (WATCHING,  "不要再加伺服器了"),
    (PLAYING,   "能動就別動它"),
    (LISTENING, "主機快撐不住了"),
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
