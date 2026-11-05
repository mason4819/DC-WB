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
    (WATCHING,  "{duty} 一個人顧 {servers} 個伺服器，怎樣"),
    (PLAYING,   "{members} 個人還不夠我忙嗎"),
    (WATCHING,  "{duty}：有事自己排隊"),
    (PLAYING,   "沒有替補，因為我不需要"),
    (LISTENING, "別吵，{duty} 正在摸魚"),
    (WATCHING,  "{duty} 上班，你們負責活著"),
    (PLAYING,   "{servers} 個伺服器而已，小事"),
    (WATCHING,  "{duty}：這點工作也要找人幫？"),
    (PLAYING,   "{members} 個人，沒一個能幫我"),
    (LISTENING, "有事找 {duty}，沒事也別找"),
    (WATCHING,  "{duty} 還沒跑路，珍惜一下"),
    (PLAYING,   "本機器人目前沒有同事，謝謝"),
    (WATCHING,  "{duty}：我不是不累，我是不敢停"),
    (PLAYING,   "免費託管，免費員工，免費受氣"),
    (LISTENING, "{duty} 已讀不回，請稍後再試"),
]

BACKUP_UP = [
    (WATCHING,  "{duty} 正在上班，{backup} 正在看戲"),
    (PLAYING,   "{backup} 在線，出事終於有人陪葬"),
    (WATCHING,  "{duty} 主班，{backup} 負責裝忙"),
    (PLAYING,   "{backup}：沒事別叫我，有事再說"),
    (LISTENING, "{duty} 正在呼叫 {backup}，請勿裝死"),
    (WATCHING,  "{backup} 已上線，摸魚人數增加一位"),
    (PLAYING,   "{duty} 顧 {servers} 個伺服器，{backup} 顧心情"),
    (WATCHING,  "{members} 位成員，兩台主機一起受罪"),
    (PLAYING,   "{duty} 和 {backup}，誰都別想下班"),
    (LISTENING, "{backup}，你的待命不是待機"),
    (WATCHING,  "{duty} 負責工作，{backup} 負責存在"),
    (PLAYING,   "有替補就是好，至少可以互相甩鍋"),
]

OFFLOAD = [
    (PLAYING,   "{backup} 又被抓去畫圖了"),
    (WATCHING,  "{duty} 把工作丟給 {backup}，漂亮"),
    (PLAYING,   "{backup}：不是說好只是待命嗎"),
    (LISTENING, "{duty} 又在催 {backup} 交圖"),
    (PLAYING,   "{backup} 正在趕圖，催也沒用"),
    (WATCHING,  "{duty} 顧伺服器，{backup} 當美術"),
    (PLAYING,   "{backup} 今天也逃不掉加班"),
    (LISTENING, "{backup} 正在畫圖，請勿打擾"),
    (PLAYING,   "{duty}：圖呢？{backup}：快了"),
    (WATCHING,  "{backup} 的 GPU 正在替大家受苦"),
    (PLAYING,   "{backup} 出賣算力，換取和平"),
    (LISTENING, "{duty} 負責催圖，{backup} 負責裝死"),
]

BACKUP_DOWN = [
    (WATCHING,  "{duty} 又要一個人上班了"),
    (PLAYING,   "{backup} 人呢？人呢？"),
    (WATCHING,  "{duty} 已經習慣沒有 {backup} 了"),
    (LISTENING, "{duty} 呼叫 {backup}，無人接聽"),
    (PLAYING,   "{backup} 是不是又睡著了"),
    (WATCHING,  "{servers} 個伺服器，全丟給 {duty}"),
    (PLAYING,   "{members} 個人，{duty} 自己想辦法"),
    (LISTENING, "{backup} 再不回來就要扣薪了"),
    (PLAYING,   "{duty}：我真的會謝"),
    (WATCHING,  "{backup} 的班表是不是排假的"),
    (PLAYING,   "所謂的備援，就是備著不用"),
    (LISTENING, "{duty} 正在單方面聯絡 {backup}"),
    (WATCHING,  "{duty} 一個人扛，{backup} 一個人消失"),
    (PLAYING,   "備援已失蹤，請勿報警，還在找"),
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
