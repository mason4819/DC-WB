"""Rotating bot status that shows current information (version, servers, members, uptime, latency,
which host is on duty). All in this one file.

This host is the one connected to Discord, so it is "on duty" ({duty} = its NODE_NAME).
The other host (learned from the heartbeat in ha.py) is the helper ({backup}). The pool of
texts changes with the situation:
  SOLO         no other host configured (or not checked yet)
  BACKUP_UP    the other host is up and on standby; OFFLOAD texts are added while it draws images for us
  BACKUP_DOWN  the other host is not answering

Edit the lists below to change the texts. Each item is (activity type, text). Placeholders:
{version} {servers} {members} {uptime} {latency} {duty} {backup}. Discord allows about 5 presence updates per 20 seconds,
so the interval (env STATUS_INTERVAL, default 30 s) must not go below 5. Set STATUS_ROTATION=false to switch it off.
Cost: one tiny gateway message every STATUS_INTERVAL seconds. No database, no HTTP requests.
"""
import logging
import math
import os
import random
import time

import discord
from discord.ext import commands, tasks

import config
import ha
from utils import remote

log = logging.getLogger(__name__)

STATUS_INTERVAL_SECONDS = config.STATUS_INTERVAL

PLAYING = discord.ActivityType.playing
WATCHING = discord.ActivityType.watching
LISTENING = discord.ActivityType.listening

COMMON = [
    (WATCHING,  "歡迎機器人 {version}"),
    (WATCHING,  "{servers} 個伺服器"),
    (WATCHING,  "{members} 位成員"),
    (PLAYING,   "已運行 {uptime}"),
    (LISTENING, "延遲 {latency}"),
    (PLAYING,   "目前主機：{duty}"),
    (PLAYING,   "輸入 /邀請 取得邀請連結"),
]

SOLO = COMMON

BACKUP_UP = COMMON + [
    (WATCHING,  "{duty} 使用中，{backup} 待命"),
    (PLAYING,   "備援主機 {backup} 已上線"),
]

OFFLOAD = [
    (PLAYING,   "{backup} 正在協助繪製歡迎圖片"),
]

BACKUP_DOWN = COMMON + [
    (WATCHING,  "備援主機 {backup} 離線中"),
]


def _uptime_text(seconds: float) -> str:
    minutes = int(seconds // 60)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days} 天 {hours} 小時"
    if hours:
        return f"{hours} 小時 {mins} 分"
    return f"{mins} 分鐘"


class StatusCog(commands.Cog, name="Status"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._mode = ""
        self._queue: list = []

    async def cog_load(self) -> None:
        if os.getenv("STATUS_ROTATION", "true").strip().lower() in {"0", "false", "no", "off"}:
            log.info("Status rotation is turned off")
            return
        self.rotate.start()

    async def cog_unload(self) -> None:
        self.rotate.cancel()

    def _pool(self) -> tuple[str, list]:
        """Pick the texts that match the situation right now."""
        if not config.PEER_URL or ha.peer["up"] is None:
            return "solo", SOLO
        if not ha.peer["up"]:
            return "down", BACKUP_DOWN
        if remote.is_enabled() and remote._down_until <= time.monotonic():
            return "up+draw", BACKUP_UP + OFFLOAD
        return "up", BACKUP_UP

    def _next_activity(self) -> discord.Activity:
        mode, pool = self._pool()
        if mode != self._mode or not self._queue:       # new situation, or one full round done
            self._mode, self._queue = mode, random.sample(pool, len(pool))
        kind, text = self._queue.pop()
        guilds = self.bot.guilds
        latency = self.bot.latency
        latency_text = f"{latency * 1000:.0f} ms" if latency is not None and math.isfinite(latency) else "—"
        text = (text.replace("{version}", config.VERSION)
                    .replace("{uptime}", _uptime_text(time.time() - config.STARTED_AT))
                    .replace("{latency}", latency_text)
                    .replace("{duty}", config.NODE_NAME)
                    .replace("{backup}", ha.peer["name"] or "另一台")
                    .replace("{servers}", str(len(guilds)))
                    .replace("{members}", str(sum(guild.member_count or 0 for guild in guilds))))
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
