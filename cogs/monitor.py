"""Bot status monitor: samples the bot every 30 seconds and keeps a line chart up to date.

- Every 30 s it records: Discord latency, memory, CPU use, server and member counts (in memory).
  Samples are saved to the database in batches (one request every MONITOR_WRITE_EVERY samples,
  default 10, and once more when the bot shuts down) and kept for 24 hours, so the history survives
  restarts and the switch to the other host. (Only the host that is connected to Discord records.)
- The chart (latency, memory, CPU) goes to ONE message in the status channel, which is edited
  in place on every refresh, so the channel is never flooded. Its id is stored in the database,
  so after a restart or a failover the same message keeps being updated.
- Settings (env): MONITOR=false switches it off; STATUS_GUILD_ID / STATUS_CHANNEL_ID choose where
  it is posted; CHART_MINUTES (default 60) is how far back the chart looks; MONITOR_POST_EVERY
  (default 4, i.e. every 2 minutes) redraws the chart every N samples; set it to 1 for every 30 s.
- Failures (missing channel, no permission) are logged at most every 10 minutes and retried.
"""
import asyncio
import io
import logging
import math
import time
from collections import deque
from typing import Optional

import discord
from discord.ext import commands, tasks

import busywork
import config
import database
from utils import chart
from utils import image as imaging
from utils.sysinfo import memory_mb

log = logging.getLogger(__name__)

RETENTION_SAMPLES = config.METRIC_RETENTION_HOURS * 3600 // config.MONITOR_INTERVAL
PRUNE_EVERY = 120            # samples (one hour)
MESSAGE_KEY = "status_message"
FILE_NAME = "status.png"


class MonitorCog(commands.Cog, name="Monitor"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.samples: deque[dict] = deque(maxlen=RETENTION_SAMPLES)
        self._ticks = 0
        self._pending: list[dict] = []          # samples not yet saved to the database
        self._posted_once = False
        self._cpu_mark: Optional[tuple[float, float]] = None
        self._message_id: Optional[int] = None
        self._retry_after = 0.0
        self._warned_at = float("-inf")

    async def cog_load(self) -> None:
        if not config.MONITOR_ENABLED:
            log.info("The status monitor is turned off")
            return
        self.sample_loop.start()

    async def cog_unload(self) -> None:
        self.sample_loop.cancel()
        try:
            await self._flush()                 # do not lose the last few samples on a handover
        except Exception:
            log.warning("Could not save the last status samples", exc_info=True)

    # ------------------------------------------------------------------ sampling
    def collect(self) -> dict:
        """One reading of this process. CPU is the share of one core used since the previous reading."""
        wall, cpu_time = time.monotonic(), time.process_time()
        cpu = None
        if self._cpu_mark is not None and wall > self._cpu_mark[0]:
            cpu = max(0.0, (cpu_time - self._cpu_mark[1]) / (wall - self._cpu_mark[0]) * 100)
        self._cpu_mark = (wall, cpu_time)
        latency = self.bot.latency
        guilds = self.bot.guilds
        return {"ts": int(time.time()), "host": config.NODE_NAME,
                "latency": latency * 1000 if latency is not None and math.isfinite(latency) else None,
                "memory": memory_mb(), "cpu": cpu, "guilds": len(guilds),
                "members": sum(guild.member_count or 0 for guild in guilds)}

    @tasks.loop(seconds=config.MONITOR_INTERVAL)
    async def sample_loop(self) -> None:
        try:
            await self.tick()
        except Exception:                      # never let one bad round stop the monitor
            log.exception("Status monitor round failed")

    @sample_loop.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_ready()
        await self._load_history()

    async def _load_history(self) -> None:
        """Continue the chart from what a previous run (or the other host) saved."""
        try:
            rows = await database.load_metrics(int(time.time()) - config.METRIC_RETENTION_HOURS * 3600)
            self.samples.extend(rows)
            stored = await database.get_asset(MESSAGE_KEY)
            self._message_id = int(stored) if stored else None
            log.info("Status monitor loaded %d earlier samples", len(rows))
        except (database.DatabaseError, ValueError):
            log.warning("Could not load the earlier status samples", exc_info=True)

    async def _flush(self) -> None:
        """Save the waiting samples in one database request. They stay queued if it fails."""
        if not self._pending:
            return
        batch = list(self._pending)
        try:
            await database.add_metrics(batch)
        except database.DatabaseError:
            self._warn("無法把狀態紀錄寫進資料庫（圖表仍會用記憶體裡的資料，稍後再試）")
            del self._pending[:-240]            # never grow without limit (keeps the last two hours)
            return
        del self._pending[:len(batch)]

    async def tick(self) -> None:
        sample = self.collect()
        self.samples.append(sample)
        self._pending.append(sample)
        self._ticks += 1
        if len(self._pending) >= config.MONITOR_WRITE_EVERY:
            await self._flush()
        if self._ticks % PRUNE_EVERY == 0:
            try:
                await database.prune_metrics(int(time.time()) - config.METRIC_RETENTION_HOURS * 3600)
            except database.DatabaseError:
                self._warn("無法清理舊的狀態紀錄")
        # The first chart goes out at once; after that every MONITOR_POST_EVERY samples.
        if not self._posted_once or self._ticks % config.MONITOR_POST_EVERY == 0:
            self._posted_once = await self.publish() or self._posted_once

    # ------------------------------------------------------------------ chart + message
    async def chart_png(self, minutes: int, minimum: int = 2) -> Optional[bytes]:
        """PNG of the last `minutes` minutes, or None when there are fewer than `minimum` samples."""
        recent = list(self.samples)
        if len(recent) < minimum:
            return None
        async with imaging.RENDER_LOCK:
            with busywork.busy():
                return await asyncio.to_thread(chart.render_status_chart, recent, minutes, None,
                                               imaging.DEFAULT_THEME, config.MONITOR_INTERVAL)

    def _warn(self, text: str) -> None:
        """Log a recurring problem at most every 10 minutes."""
        if time.monotonic() - self._warned_at > 600:
            self._warned_at = time.monotonic()
            log.warning("Status monitor: %s", text)

    def _channel(self) -> Optional[discord.TextChannel]:
        guild = self.bot.get_guild(config.STATUS_GUILD_ID)
        if guild is None:
            self._warn(f"機器人不在伺服器 {config.STATUS_GUILD_ID}，無法發送狀態圖")
            return None
        channel = guild.get_channel(config.STATUS_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            self._warn(f"伺服器 {guild.name} 裡找不到文字頻道 {config.STATUS_CHANNEL_ID}")
            return None
        return channel

    def _embed(self, sample: dict) -> discord.Embed:
        def fmt(value: Optional[float], unit: str) -> str:
            return "—" if value is None else f"{value:.0f} {unit}" if value >= 10 else f"{value:.1f} {unit}"
        embed = discord.Embed(title="機器人狀態", colour=config.WELCOME_COLOR)
        embed.description = (f"主機：**{sample['host']}**（使用中）　運行 {int((time.time() - config.STARTED_AT) // 3600)} 小時\n"
                             f"延遲 {fmt(sample['latency'], 'ms')}　記憶體 {fmt(sample['memory'], 'MB')}　CPU {fmt(sample['cpu'], '%')}\n"
                             f"{sample['guilds']} 個伺服器　{sample['members']} 位成員\n"
                             f"更新：<t:{sample['ts']}:R>")
        embed.set_image(url=f"attachment://{FILE_NAME}")
        embed.set_footer(text=f"每 {config.MONITOR_INTERVAL} 秒記錄一次，每 {config.MONITOR_INTERVAL * config.MONITOR_POST_EVERY} 秒更新圖表　圖表為最近 {config.CHART_MINUTES} 分鐘")
        return embed

    async def publish(self) -> bool:
        """Draw the chart and edit the status message (or create it the first time). True on success."""
        if time.monotonic() < self._retry_after:
            return False
        channel = self._channel()
        if channel is None:
            self._retry_after = time.monotonic() + 60
            return False
        png = await self.chart_png(config.CHART_MINUTES, minimum=1)     # the very first one says "collecting data"
        if png is None:
            return False
        embed = self._embed(self.samples[-1])
        try:
            if self._message_id is not None:
                try:
                    await channel.get_partial_message(self._message_id).edit(
                        embed=embed, attachments=[discord.File(io.BytesIO(png), filename=FILE_NAME)])
                    return True
                except discord.NotFound:                       # somebody deleted it: post a fresh one
                    self._message_id = None
            message = await channel.send(embed=embed, file=discord.File(io.BytesIO(png), filename=FILE_NAME),
                                         allowed_mentions=discord.AllowedMentions.none())
            self._message_id = message.id
            try:
                await database.set_asset(MESSAGE_KEY, str(message.id).encode())
            except database.DatabaseError:
                self._warn("無法記住狀態訊息的 ID（重啟後會重新發一則）")
            return True
        except discord.Forbidden:
            self._warn(f"沒有權限在 #{channel.name} 發送或編輯狀態圖（需要發送訊息、嵌入連結、附加檔案）")
            self._retry_after = time.monotonic() + 600
            return False
        except discord.HTTPException as exc:
            self._warn(f"發送狀態圖失敗（Discord 回傳 {exc.status}）")
            self._retry_after = time.monotonic() + 120
            return False


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MonitorCog(bot))
