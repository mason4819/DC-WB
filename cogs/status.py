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
    (WATCHING,  "目前由 {duty} 一台獨自撐場"),
    (PLAYING,   "伺服器有 {members} 位觀眾，壓力山大"),
    (LISTENING, "啊不就好棒棒，只有一台在上班"),
    (WATCHING,  "目前在線主機：{servers}"),
    (PLAYING,   "笑死，這也能正常運作"),
    (WATCHING,  "各位，今天也是平安的一天"),
    (LISTENING, "蛤？又有新工作？"),
    (PLAYING,   "{duty} 辛苦了，我先在旁邊看"),
    (WATCHING,  "人口 {members}，責任無上限"),
    (PLAYING,   "沒人替班是怎樣啦"),
    (LISTENING, "是在哈囉"),
    (WATCHING,  "伺服器沒炸就先不要吵"),
    (PLAYING,   "這年頭連機器人都要兼差"),
]

BACKUP_UP = [
    (WATCHING,  "目前有 {servers} 台主機在線"),
    (PLAYING,   "哇，今天居然有替補"),
    (LISTENING, "兩台主機，雙倍的快樂"),
    (WATCHING,  "{duty} 正在值班，{backup} 負責待命"),
    (PLAYING,   "有福同享，有鍋同背"),
    (WATCHING,  "目前服務 {members} 位觀眾"),
    (PLAYING,   "這波穩了吧？應該啦"),
    (LISTENING, "各位冷靜，先不要高興太早"),
    (WATCHING,  "兩個免費仔撐起一片天"),
    (PLAYING,   "人多不一定有用，至少看起來很忙"),
    (WATCHING,  "今天終於不用單打獨鬥"),
    (LISTENING, "這就是所謂的團隊合作"),
]

OFFLOAD = [
    (PLAYING,   "{backup} 又被抓去畫圖了"),
    (WATCHING,  "目前有 {servers} 台主機在忙"),
    (LISTENING, "GPU：我上輩子造了什麼孽"),
    (PLAYING,   "免費美術部門正式開工"),
    (WATCHING,  "{duty} 負責服務，{backup} 負責苦力"),
    (PLAYING,   "這張圖到底要畫到民國幾年"),
    (LISTENING, "是在催三小，圖還沒好"),
    (WATCHING,  "目前服務 {members} 位觀眾，還要畫圖"),
    (PLAYING,   "這不是加班，這是福報"),
    (LISTENING, "好了啦，不要再催了"),
]

BACKUP_DOWN = [
    (WATCHING,  "欸不是，{backup} 人呢？"),
    (PLAYING,   "目前只剩 {duty} 在撐"),
    (LISTENING, "說好的備援去哪了"),
    (WATCHING,  "目前在線主機：{servers}"),
    (PLAYING,   "笑死，備援系統查無此人"),
    (LISTENING, "有人知道另一台去哪裡了嗎"),
    (WATCHING,  "目前還有 {members} 位觀眾"),
    (PLAYING,   "一個人扛全部，合理嗎"),
    (LISTENING, "救命啊，真的沒人了"),
    (WATCHING,  "這劇情我是不是看過"),
    (PLAYING,   "有備援跟沒備援一樣，哭啊"),
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
