"""Rotating bot status that says who is on duty and who is backing up. All in this one file.

This host is the one connected to Discord, so it is "on duty" ({duty} = its NODE_NAME).
The other host (learned from the heartbeat in ha.py) is the helper ({backup}). The pool of
texts changes with the situation:
  SOLO         no other host configured (or not checked yet)
  BACKUP_UP    the other host is up and on standby; OFFLOAD texts are added while it draws images for us
  BACKUP_DOWN  the other host is not answering

Edit the lists below to change the texts. Each item is (activity type, text). Placeholders:
{duty} {backup} {servers} {members}. Discord allows about 5 presence updates per 20 seconds,
so STATUS_INTERVAL_SECONDS must not go below 5. Set STATUS_ROTATION=false to switch it off.
Cost: one tiny gateway message every few seconds. No database, no HTTP requests.
"""
import logging
import os
import random
import time

import discord
from discord.ext import commands, tasks

import config
import ha
from utils import remote

log = logging.getLogger(__name__)

STATUS_INTERVAL_SECONDS = 5

PLAYING = discord.ActivityType.playing
WATCHING = discord.ActivityType.watching
LISTENING = discord.ActivityType.listening

SOLO = [
    (WATCHING,  "{duty} 顧著 {servers} 個伺服器"),
    (PLAYING,   "{members} 個人都在等 {duty}"),
    (WATCHING,  "{duty} 今天又沒人替班"),
    (PLAYING,   "{duty}：怎麼又是我"),
    (LISTENING, "{duty} 的下班時間遙遙無期"),
    (WATCHING,  "{duty} 看著 {servers} 個伺服器發呆"),
    (PLAYING,   "{duty} 還活著，伺服器就還活著"),
    (WATCHING,  "{members} 個人，真的顧得完嗎"),
    (PLAYING,   "{duty} 正在等一個永遠不會發生的下班"),
    (LISTENING, "又有人找 {duty} 了"),
]

BACKUP_UP = [
    (WATCHING,  "{duty} 上班，{backup} 蹺班"),
    (PLAYING,   "{backup} 在線，先不用怕"),
    (WATCHING,  "{duty} 工作，{backup} 監工"),
    (PLAYING,   "{backup} 今天最好不要出事"),
    (LISTENING, "{duty} 正在叫 {backup} 起床"),
    (WATCHING,  "{backup}：有事再叫我"),
    (PLAYING,   "{duty} 和 {backup} 都別想下班"),
    (WATCHING,  "{servers} 個伺服器，兩個人輪流受苦"),
    (PLAYING,   "{members} 個人，兩台機器在扛"),
    (LISTENING, "{backup} 有聽到嗎，該你了"),
]

OFFLOAD = [
    (PLAYING,   "{backup} 又被抓去畫圖了"),
    (WATCHING,  "{duty} 把畫圖工作丟給 {backup}"),
    (PLAYING,   "{backup}：不是說好只待命嗎"),
    (LISTENING, "{duty} 又在催 {backup} 交圖"),
    (PLAYING,   "{backup} 正在趕圖，別催"),
    (WATCHING,  "{duty} 負責顧人，{backup} 負責畫圖"),
    (PLAYING,   "{backup} 今天也逃不掉"),
]

BACKUP_DOWN = [
    (WATCHING,  "{duty} 又要一個人上班了"),
    (PLAYING,   "{backup} 人呢？人呢？"),
    (WATCHING,  "{duty} 已經習慣沒有 {backup} 了"),
    (LISTENING, "{duty} 呼叫 {backup}，沒反應"),
    (PLAYING,   "{backup} 是不是又睡著了"),
    (WATCHING,  "{servers} 個伺服器，全丟給 {duty}"),
    (PLAYING,   "{members} 個人，{duty} 自己想辦法"),
    (LISTENING, "{backup} 再不回來就要扣薪了"),
    (PLAYING,   "{duty}：我真的會謝"),
    (WATCHING,  "{backup} 的班表是不是排假的"),
]


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
        text = (text.replace("{duty}", config.NODE_NAME)
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
