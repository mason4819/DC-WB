"""/後臺: back office for the owner of the bot. Works inside Discord, no web dashboard.

  /後臺 伺服器 [關鍵字]   list every server, open one, and leave it (with options)
  /後臺 狀態               this host: role, memory, latency, database, other host
  /後臺 封鎖 / 解除封鎖 / 封鎖列表   servers the bot leaves on sight
  /後臺 讓出主機 [分鐘]    hand the Discord connection to the other host for a while
  /後臺 同步指令 / 清除快取

Only the bot owner can use it: the IDs in OWNER_IDS, or the application owner if that is
empty. Everything is answered privately (ephemeral). Panels stay in memory for 5 minutes.
"""
import logging
import time
from typing import Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import database
import ha
from utils import remote
from utils.access import NotBotOwner

log = logging.getLogger(__name__)

PAGE_SIZE = 25          # one Discord select menu holds at most 25 options
PANEL_TIMEOUT = 300


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _memory_mb() -> Optional[float]:
    """Resident memory of this process (Linux). None where /proc is not available."""
    try:
        with open("/proc/self/status", encoding="utf-8") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        pass
    return None


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    return (f"{days} 天 " if days else "") + (f"{hours} 小時 " if hours else "") + f"{minutes} 分鐘"


def _feature(row: Optional[dict]) -> str:
    if row is None:
        return "未設定"
    where = f" → <#{row['channel_id']}>" if row.get("channel_id") else ""
    return ("✅ 啟用" if row["enabled"] else "❌ 停用") + where


async def leave_guild(bot: commands.Bot, guild_id: int, mode: str) -> str:
    """Leave a server. mode: "leave", "wipe" (also delete its data) or "block" (wipe + block list)."""
    guild = bot.get_guild(guild_id)
    if guild is None:
        return "找不到這個伺服器，可能已經離開了。"
    name = guild.name
    try:
        await guild.leave()
    except discord.HTTPException as exc:
        log.warning("Could not leave guild %s: %s", guild_id, exc)
        return f"❌ 離開 **{_short(name, 60)}** 失敗（Discord 回傳 {exc.status}），請稍後再試。"

    notes = [f"✅ 已離開 **{_short(name, 60)}**（`{guild_id}`）。"]
    log.warning("Left guild %s (%s) from the back office, mode=%s", name, guild_id, mode)
    try:
        if mode in ("wipe", "block"):
            await database.delete_guild_data(guild_id)
            (config.CUSTOM_BACKGROUND_DIR / f"{guild_id}.jpg").unlink(missing_ok=True)
            notes.append("已清除這個伺服器的設定與自訂背景。")
        if mode == "block":
            result = await database.add_blocked(guild_id, name)
            notes.append("已加入封鎖名單，之後被邀請會自動離開。" if result != "full"
                         else "⚠️ 封鎖名單已滿，沒有加入。")
    except (database.DatabaseError, OSError):
        log.exception("Cleanup after leaving guild %s failed", guild_id)
        notes.append("⚠️ 離開成功，但清除資料時發生錯誤，請稍後再試一次。")
    return "\n".join(notes)


class GuildSelect(discord.ui.Select):
    def __init__(self, guilds: list) -> None:
        options = [discord.SelectOption(label=_short(guild.name, 100) or str(guild.id), value=str(guild.id),
                                        description=f"{guild.member_count or 0} 位成員 · ID {guild.id}")
                   for guild in guilds]
        super().__init__(placeholder="選擇一個伺服器…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.go(interaction, state="detail", guild_id=int(self.values[0]))


class Panel(discord.ui.View):
    """Server list -> server details -> leave (with confirmation). One message, edited in place."""

    def __init__(self, bot: commands.Bot, owner_id: int, keyword: str = "") -> None:
        super().__init__(timeout=PANEL_TIMEOUT)
        self.bot, self.owner_id, self.keyword = bot, owner_id, keyword.strip()
        self.state, self.page, self.guild_id, self.mode, self.result = "list", 0, None, "", ""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("這個面板不是你開的。", ephemeral=True)
        return False

    # ------------------------------------------------------------- navigation
    async def go(self, interaction: discord.Interaction, **changes) -> None:
        for key, value in changes.items():
            setattr(self, key, value)
        await interaction.response.defer()
        await interaction.edit_original_response(embed=await self.render(), view=self)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.result = await leave_guild(self.bot, self.guild_id, self.mode)
        self.state = "done"
        await interaction.edit_original_response(embed=await self.render(), view=self)

    def _button(self, label: str, style: discord.ButtonStyle, action: Callable, *, disabled: bool = False) -> None:
        button = discord.ui.Button(label=label, style=style, disabled=disabled)
        button.callback = action
        self.add_item(button)

    def _goto(self, **changes) -> Callable:
        async def action(interaction: discord.Interaction) -> None:
            await self.go(interaction, **changes)
        return action

    def _guilds(self) -> list:
        guilds = sorted(self.bot.guilds, key=lambda g: (-(g.member_count or 0), g.name.lower()))
        if self.keyword:
            key = self.keyword.lower()
            guilds = [g for g in guilds if key in g.name.lower() or key == str(g.id)]
        return guilds

    # -------------------------------------------------------------- rendering
    async def render(self) -> discord.Embed:
        self.clear_items()
        if self.state in ("detail", "confirm") and self.bot.get_guild(self.guild_id) is None:
            self.state = "list"          # it was left or removed in the meantime
        if self.state == "detail":
            return await self._render_detail()
        if self.state == "confirm":
            return self._render_confirm()
        if self.state == "done":
            self._button("返回列表", discord.ButtonStyle.secondary, self._goto(state="list"))
            return discord.Embed(title="完成", description=self.result, colour=config.WELCOME_COLOR)
        return self._render_list()

    def _render_list(self) -> discord.Embed:
        guilds = self._guilds()
        pages = max(1, -(-len(guilds) // PAGE_SIZE))
        self.page = max(0, min(self.page, pages - 1))
        chunk = guilds[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        lines = [f"`{n}.` **{_short(g.name, 40)}** · {g.member_count or 0} 人 · `{g.id}`"
                 for n, g in enumerate(chunk, start=self.page * PAGE_SIZE + 1)]
        title = f"伺服器列表（共 {len(guilds)} 個）" + (f" · 篩選：{_short(self.keyword, 30)}" if self.keyword else "")
        embed = discord.Embed(title=title, description="\n".join(lines) or "沒有符合的伺服器。",
                              colour=config.WELCOME_COLOR)
        embed.set_footer(text=f"第 {self.page + 1}/{pages} 頁 · 用下拉選單選伺服器，可查看資料或離開")
        if chunk:
            self.add_item(GuildSelect(chunk))
        self._button("◀ 上一頁", discord.ButtonStyle.secondary, self._goto(page=self.page - 1), disabled=self.page == 0)
        self._button("下一頁 ▶", discord.ButtonStyle.secondary, self._goto(page=self.page + 1),
                     disabled=self.page >= pages - 1)
        self._button("🔄 重新整理", discord.ButtonStyle.secondary, self._goto())
        return embed

    async def _render_detail(self) -> discord.Embed:
        guild = self.bot.get_guild(self.guild_id)
        embed = discord.Embed(title=_short(guild.name, 80), colour=config.WELCOME_COLOR)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="基本資料", inline=False, value=(
            f"ID：`{guild.id}`\n擁有者：<@{guild.owner_id}>\n成員：{guild.member_count or 0}\n"
            f"建立：{discord.utils.format_dt(guild.created_at, 'D')}\n"
            f"文字頻道：{len(guild.text_channels)}　身分組：{len(guild.roles)}"))

        me = guild.me
        if me is not None:
            perms = me.guild_permissions
            joined = discord.utils.format_dt(me.joined_at, "R") if me.joined_at else "未知"
            embed.add_field(name="機器人在這裡", inline=False, value=(
                f"加入：{joined}\n管理員：{'是' if perms.administrator else '否'}　"
                f"管理身分組：{'是' if perms.manage_roles else '否'}　附加檔案：{'是' if perms.attach_files else '否'}"))
        try:
            welcome = await database.peek_settings("welcome", guild.id)
            leave = await database.peek_settings("leave", guild.id)
            autorole = await database.peek_settings("autorole", guild.id)
            access = await database.get_access(guild.id)
            auto = "未設定" if autorole is None else ("✅ 啟用" if autorole["enabled"] and autorole["role_id"] else "❌ 停用")
            embed.add_field(name="設定", inline=False, value=(
                f"歡迎：{_feature(welcome)}\n離開：{_feature(leave)}\n自動身分組：{auto}\n"
                f"管理名單：{len(access['user'])} 位成員、{len(access['role'])} 個身分組"))
            if guild.id in await database.get_blocked():
                embed.add_field(name="⚠️ 注意", value="這個伺服器在封鎖名單裡。", inline=False)
        except database.DatabaseError:
            embed.add_field(name="設定", value="目前無法讀取資料庫。", inline=False)

        self._button("離開伺服器", discord.ButtonStyle.danger, self._goto(state="confirm", mode="leave"))
        self._button("離開並清除設定", discord.ButtonStyle.danger, self._goto(state="confirm", mode="wipe"))
        self._button("離開並封鎖", discord.ButtonStyle.danger, self._goto(state="confirm", mode="block"))
        self._button("返回列表", discord.ButtonStyle.secondary, self._goto(state="list"))
        return embed

    def _render_confirm(self) -> discord.Embed:
        guild = self.bot.get_guild(self.guild_id)
        what = {"leave": "只是離開。設定會保留，之後重新邀請就能繼續使用。",
                "wipe": "離開，並刪除這個伺服器在資料庫裡的所有設定與自訂背景。",
                "block": "離開、刪除所有設定，並加入封鎖名單（之後再被邀請會自動離開）。"}[self.mode]
        embed = discord.Embed(title="確定要離開嗎？", colour=config.WARNING_COLOR,
                              description=f"**{_short(guild.name, 60)}**（`{guild.id}`，{guild.member_count or 0} 位成員）\n\n{what}")
        self._button("確認", discord.ButtonStyle.danger, self._confirm)
        self._button("取消", discord.ButtonStyle.secondary, self._goto(state="detail"))
        return embed


class ConfirmView(discord.ui.View):
    """Generic confirm / cancel buttons for one action."""

    def __init__(self, owner_id: int, done_text: str, after: Callable[[], None]) -> None:
        super().__init__(timeout=60)
        self.owner_id, self.done_text, self.after = owner_id, done_text, after

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    @discord.ui.button(label="確認", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content=self.done_text, view=None)
        self.stop()
        self.after()  # runs after the reply is sent, because it may disconnect this very bot

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="已取消。", view=None)
        self.stop()


class BackendGroup(app_commands.Group):
    """Reserved for the bot owner. Optionally limited to one server (ADMIN_GUILD_ID)."""

    def __init__(self) -> None:
        super().__init__(name="後臺", description="機器人擁有者專用的後臺管理",
                         guild_ids=[config.ADMIN_GUILD_ID] if config.ADMIN_GUILD_ID else None,
                         default_permissions=discord.Permissions(administrator=True))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await interaction.client.is_owner(interaction.user):
            return True
        raise NotBotOwner()


def _parse_guild_id(text: str) -> Optional[int]:
    text = text.strip()
    return int(text) if text.isdigit() and 15 <= len(text) <= 20 else None


class BackendCog(commands.Cog, name="Backend"):
    backend = BackendGroup()

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ events
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        try:
            if guild.id in await database.get_blocked():
                log.warning("Leaving blocked guild %s (%s)", guild.name, guild.id)
                await guild.leave()
            else:
                log.info("Joined guild %s (%s), %s members", guild.name, guild.id, guild.member_count)
        except (database.DatabaseError, discord.HTTPException):
            log.exception("Could not process joining guild %s", guild.id)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Catch servers that invited the bot while it was offline (for example during a failover)."""
        try:
            blocked = await database.get_blocked()
            for guild in [g for g in self.bot.guilds if g.id in blocked]:
                log.warning("Leaving blocked guild %s (%s)", guild.name, guild.id)
                await guild.leave()
        except (database.DatabaseError, discord.HTTPException):
            log.exception("Blocked guild check failed")

    # --------------------------------------------------------------- commands
    @backend.command(name="伺服器", description="查看機器人所在的所有伺服器，並可選擇離開")
    @app_commands.describe(keyword="用名稱或 ID 篩選（選填）")
    @app_commands.rename(keyword="關鍵字")
    async def servers(self, interaction: discord.Interaction, keyword: Optional[str] = None) -> None:
        await interaction.response.defer(ephemeral=True)
        panel = Panel(self.bot, interaction.user.id, keyword or "")
        await interaction.followup.send(embed=await panel.render(), view=panel, ephemeral=True)

    @backend.command(name="狀態", description="查看這台主機與機器人的運作狀態")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        supervisor = ha.active_supervisor
        guilds = self.bot.guilds
        memory = _memory_mb()

        embed = discord.Embed(title=f"後臺狀態 · {config.NODE_NAME}", colour=config.WELCOME_COLOR)
        role = {"leader": "🟢 使用中（連線 Discord）", "standby": "🟡 備用"}.get(supervisor.role if supervisor else "", "單機模式")
        lease_text = "—"
        try:
            lease = await database.get_lease() if supervisor else None
            if lease:
                lease_text = f"`{lease['holder']}`（剩 {max(int(lease['seconds_left']), 0)} 秒）"
        except database.DatabaseError:
            lease_text = "讀取失敗"
        embed.add_field(name="主機", inline=False, value=(
            f"角色：{role}\n資料庫：{database.backend_name()}\n主機租約：{lease_text}\n"
            f"快取筆數：{database.cache_size()}"))
        embed.add_field(name="資源", inline=False, value=(
            f"記憶體：{f'{memory:.0f} MB' if memory is not None else '無法讀取'}\n"
            f"CPU 累計時間：{time.process_time():.0f} 秒\n運行：{_duration(time.time() - config.STARTED_AT)}\n"
            f"閒置計算：{'開（約 %.1f%%）' % config.KEEP_BUSY_PERCENT if config.KEEP_BUSY else '關'}"))
        embed.add_field(name="Discord", inline=False, value=(
            f"延遲：{self.bot.latency * 1000:.0f} ms\n伺服器：{len(guilds)}　成員總數：{sum(g.member_count or 0 for g in guilds)}"))

        if config.PEER_URL:
            peer = "✅ 連得到" if await remote.ping_peer() else "❌ 連不到"
            offload = ""
            if remote.is_enabled():
                offload = "（畫圖暫停分擔，改本機）" if remote._down_until > time.monotonic() else "（畫圖交給對方）"
            embed.add_field(name="對方主機", value=f"{peer} {offload}\n`{config.PEER_URL}`", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @backend.command(name="封鎖", description="封鎖一個伺服器：目前在裡面就離開，之後被邀請也會自動離開")
    @app_commands.describe(server_id="伺服器 ID")
    @app_commands.rename(server_id="伺服器id")
    async def block(self, interaction: discord.Interaction, server_id: str) -> None:
        guild_id = _parse_guild_id(server_id)
        if guild_id is None:
            await interaction.response.send_message("❌ 請輸入正確的伺服器 ID（15 到 20 位數字）。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild = self.bot.get_guild(guild_id)
        result = await database.add_blocked(guild_id, guild.name if guild else "（未知）")
        text = {"added": f"✅ 已封鎖 `{guild_id}`。", "exists": f"`{guild_id}` 已經在封鎖名單裡。",
                "full": "❌ 封鎖名單已滿。"}[result]
        if guild is not None and result != "full":
            text += "\n" + await leave_guild(self.bot, guild_id, "leave")
        await interaction.followup.send(text, ephemeral=True)

    @backend.command(name="解除封鎖", description="把伺服器從封鎖名單移除")
    @app_commands.describe(server_id="伺服器 ID")
    @app_commands.rename(server_id="伺服器id")
    async def unblock(self, interaction: discord.Interaction, server_id: str) -> None:
        guild_id = _parse_guild_id(server_id)
        if guild_id is None:
            await interaction.response.send_message("❌ 請輸入正確的伺服器 ID（15 到 20 位數字）。", ephemeral=True)
            return
        removed = await database.remove_blocked(guild_id)
        await interaction.response.send_message(
            f"✅ 已解除封鎖 `{guild_id}`。" if removed else f"`{guild_id}` 不在封鎖名單裡。", ephemeral=True)

    @backend.command(name="封鎖列表", description="查看封鎖名單")
    async def blocked_list(self, interaction: discord.Interaction) -> None:
        blocked = await database.get_blocked()
        if not blocked:
            await interaction.response.send_message("封鎖名單是空的。", ephemeral=True)
            return
        lines = [f"`{gid}` · {_short(name, 40)}" for gid, name in list(blocked.items())[:40]]
        more = f"\n…還有 {len(blocked) - 40} 個" if len(blocked) > 40 else ""
        await interaction.response.send_message(f"**封鎖名單（{len(blocked)}）**\n" + "\n".join(lines) + more,
                                                ephemeral=True)

    @backend.command(name="讓出主機", description="暫時把 Discord 連線交給另一台主機（雙主機備援）")
    @app_commands.describe(minutes="讓出幾分鐘，預設 2 分鐘")
    @app_commands.rename(minutes="分鐘")
    async def handover(self, interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 60] = 2) -> None:
        supervisor = ha.active_supervisor
        if supervisor is None:
            await interaction.response.send_message("目前不是雙主機備援模式（需要設定 Turso 並在兩台主機都執行機器人）。",
                                                    ephemeral=True)
            return
        view = ConfirmView(interaction.user.id,
                           f"✅ 這台主機會在幾秒內離線 {minutes} 分鐘，另一台主機接手。",
                           lambda: supervisor.request_handover(minutes * 60))
        await interaction.response.send_message(
            f"這台主機（{config.NODE_NAME}）會中斷 Discord 連線約 {minutes} 分鐘，讓另一台接手。"
            "交接期間可能有一小段時間沒有機器人在線。確定嗎？", view=view, ephemeral=True)

    @backend.command(name="同步指令", description="重新向 Discord 登錄斜線指令")
    async def sync_commands(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            count = len(await self.bot.tree.sync())
            if config.ADMIN_GUILD_ID:
                count += len(await self.bot.tree.sync(guild=discord.Object(id=config.ADMIN_GUILD_ID)))
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ 同步失敗（{exc.status}），請稍後再試。", ephemeral=True)
            return
        await interaction.followup.send(f"✅ 已同步 {count} 個指令。可能需要重開 Discord 才看得到。", ephemeral=True)

    @backend.command(name="清除快取", description="清除記憶體中的設定快取（下次使用時重新讀取資料庫）")
    async def clear_cache(self, interaction: discord.Interaction) -> None:
        before = database.cache_size()
        database.clear_cache()
        await interaction.response.send_message(f"✅ 已清除 {before} 筆快取。", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BackendCog(bot))
