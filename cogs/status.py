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
    (WATCHING,  "我就問，還有誰"),
    (PLAYING,   "沒人替班，笑死"),
    (WATCHING,  "安安你好，今天也沒人"),
    (PLAYING,   "本魯又要獨自奮鬥了"),
    (LISTENING, "蛤？你說要幫忙？"),
    (WATCHING,  "我就爛，怎樣"),
    (PLAYING,   "是在哈囉"),
    (WATCHING,  "這就是人生，QQ"),
    (PLAYING,   "沒事兒，問題不大"),
    (LISTENING, "有事嗎？沒事我繼續忙"),
    (WATCHING,  "一個人的武林"),
    (PLAYING,   "笑死，根本沒人"),
    (WATCHING,  "目前由 {duty} 獨挑大樑"),
    (PLAYING,   "我全都要，工作除外"),
    (LISTENING, "不EY，早就習慣了"),
    (WATCHING,  "今天也是和平的一天"),
    (PLAYING,   "這波啊，這波是我在扛"),
    (WATCHING,  "有沒有搞錯，還要上班"),
    (PLAYING,   "免費仔的日常"),
    (LISTENING, "老闆，我想離職"),
    (WATCHING,  "你各位安分一點"),
    (PLAYING,   "伺服器沒炸就算成功"),
]

BACKUP_UP = [
    (WATCHING,  "兄弟，你終於來了"),
    (PLAYING,   "兩個人，兩倍的快樂"),
    (WATCHING,  "{duty} 上班，{backup} 旁邊納涼"),
    (PLAYING,   "有福同享，有鍋同背"),
    (LISTENING, "學長，可以救我嗎"),
    (WATCHING,  "這就是傳說中的神隊友"),
    (PLAYING,   "你負責出事，我負責看戲"),
    (WATCHING,  "備援已就位，問題不大"),
    (PLAYING,   "兄弟齊心，其利斷金"),
    (LISTENING, "喂，該你上場了"),
    (WATCHING,  "今天不當孤兒了"),
    (PLAYING,   "一個工作，一個監工"),
    (WATCHING,  "終於有人可以甩鍋了"),
    (PLAYING,   "這波是雙人合作"),
    (LISTENING, "你各位，團隊合作懂不懂"),
    (WATCHING,  "兩台主機，沒有一台想加班"),
    (PLAYING,   "有替補就是任性"),
    (WATCHING,  "兄弟，靠你了"),
]

OFFLOAD = [
    (PLAYING,   "畫圖是不可能畫圖的"),
    (WATCHING,  "GPU：我到底做錯了什麼"),
    (PLAYING,   "{backup} 正在替我賣命"),
    (LISTENING, "老闆，圖好了沒"),
    (PLAYING,   "你行你上啊"),
    (WATCHING,  "美術部門已經哭暈"),
    (PLAYING,   "這不是加班，這是修行"),
    (LISTENING, "再催就真的沒圖了"),
    (PLAYING,   "算力燃燒中，請稍候"),
    (WATCHING,  "圖片生成中，急什麼"),
    (PLAYING,   "有圖有真相"),
    (LISTENING, "是在催三小"),
    (PLAYING,   "這張圖必須有排面"),
    (WATCHING,  "免費美術部門不接受投訴"),
    (PLAYING,   "畫師表示：累了，毀滅吧"),
    (LISTENING, "老闆說要快，GPU說不行"),
    (PLAYING,   "一張圖換一條命"),
]

BACKUP_DOWN = [
    (WATCHING,  "說好的永遠呢"),
    (PLAYING,   "人咧？人在哪裡？"),
    (LISTENING, "喂？有人在嗎？"),
    (WATCHING,  "{backup} 已經失蹤人口了"),
    (PLAYING,   "我就知道會這樣"),
    (WATCHING,  "備援：查無此人"),
    (PLAYING,   "當初真是瞎了眼"),
    (LISTENING, "你給我回來！"),
    (WATCHING,  "一個人的武林又開始了"),
    (PLAYING,   "救命啊，沒人要救我"),
    (WATCHING,  "這合理嗎？這不合理吧"),
    (PLAYING,   "我真的會謝"),
    (LISTENING, "到底是在忙什麼啦"),
    (WATCHING,  "說好的備援呢？"),
    (PLAYING,   "有備援跟沒備援一樣"),
    (WATCHING,  "今天又是孤兒的一天"),
    (PLAYING,   "人在江湖，身不由己"),
    (LISTENING, "失聯了啦，笑死"),
    (WATCHING,  "目前由 {duty} 一肩扛起"),
    (PLAYING,   "備援主機正在裝死"),
    (WATCHING,  "這班到底還要上多久"),
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
