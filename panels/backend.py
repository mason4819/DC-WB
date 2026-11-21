"""Back office pages of the /設定 panel. Only the user IDs in OWNER_IDS may use them.

Every page checks that again on every click, so even a stale or shared panel cannot be used by
anyone else. Pages: status (home), servers, block list, status chart, hand the connection to
the other host, the loading image, sync commands, clear cache.
"""
import asyncio
import io
import logging
import time
from typing import Callable, Optional

import discord

import config
import database
import ha
from panels.base import (DANGER, PRIMARY, PanelModal, PanelView, go_home, make_button, reject,
                         require_bot_owner, short, show)
from utils import assets_store, cmdsync, remote
from utils import image as imaging
from utils.sysinfo import memory_mb

log = logging.getLogger(__name__)

PAGE_SIZE = 25          # one Discord select menu holds at most 25 options
MOTIONS = {"drift": "各自飄動", "rotate": "整體旋轉", "none": "靜態"}


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
    return ("啟用" if row["enabled"] else "停用") + where


def _parse_guild_id(text: str) -> Optional[int]:
    text = text.strip()
    return int(text) if text.isdigit() and 15 <= len(text) <= 20 else None


async def leave_guild(bot, guild_id: int, mode: str) -> str:
    """Leave a server. mode: "leave", "wipe" (also delete its data) or "block" (wipe + block list)."""
    guild = bot.get_guild(guild_id)
    if guild is None:
        return "找不到這個伺服器，可能已經離開了。"
    name = guild.name
    try:
        await guild.leave()
    except discord.HTTPException as exc:
        log.warning("Could not leave guild %s: %s", guild_id, exc)
        return f"離開 **{short(name, 60)}** 失敗（Discord 回傳 {exc.status}），請稍後再試。"

    notes = [f"已離開 **{short(name, 60)}**（`{guild_id}`）。"]
    log.warning("Left guild %s (%s) from the back office, mode=%s", name, guild_id, mode)
    try:
        if mode in ("wipe", "block"):
            await database.delete_guild_data(guild_id)          # includes the stored background
            assets_store.drop_background_cache(guild_id)
            notes.append("已清除這個伺服器的設定與自訂背景。")
        if mode == "block":
            result = await database.add_blocked(guild_id, name)
            notes.append("已加入封鎖名單，之後被邀請會自動離開。" if result != "full" else "封鎖名單已滿，沒有加入。")
    except (database.DatabaseError, OSError):
        log.exception("Cleanup after leaving guild %s failed", guild_id)
        notes.append("離開成功，但清除資料時發生錯誤，請稍後再試一次。")
    return "\n".join(notes)


async def status_blocks(bot) -> list[str]:
    """The status text of the back office home, as a few short blocks."""
    supervisor = ha.active_supervisor
    guilds = bot.guilds
    memory = memory_mb()
    role = {"leader": "使用中（連線 Discord）", "standby": "備用"}.get(supervisor.role if supervisor else "", "單機模式")
    lease_text = "無"
    try:
        lease = await database.get_lease() if supervisor else None
        if lease:
            lease_text = f"`{lease['holder']}`（剩 {max(int(lease['seconds_left']), 0)} 秒）"
    except database.DatabaseError:
        lease_text = "讀取失敗"
    blocks = [
        f"**主機　{config.NODE_NAME}**\n角色：{role}\n資料庫：{database.backend_name()}\n主機租約：{lease_text}"
        f"\n快取筆數：{database.cache_size()}",
        f"**資源**\n記憶體：{f'{memory:.0f} MB' if memory is not None else '無法讀取'}"
        f"　CPU 累計：{time.process_time():.0f} 秒\n運行：{_duration(time.time() - config.STARTED_AT)}"
        f"　閒置計算：{'開（約 %.1f%%）' % config.KEEP_BUSY_PERCENT if config.KEEP_BUSY else '關'}",
        f"**Discord**\n延遲：{bot.latency * 1000:.0f} ms　伺服器：{len(guilds)}"
        f"　成員總數：{sum(g.member_count or 0 for g in guilds)}",
    ]
    if config.PEER_URL:
        if ha.peer["checked"] is None:           # the heartbeat has not run yet: look once now
            info = await remote.check_peer()
            ha.peer.update(up=info is not None, since=time.time(), checked=time.time(),
                           name=(info or {}).get("node"), role=(info or {}).get("role"))
        now = time.time()
        who = f"{ha.peer['name']} " if ha.peer["name"] else ""
        duty = {"leader": "（使用中）", "standby": "（待命）"}.get(ha.peer["role"] or "", "")
        state = (who + "連得到" + duty) if ha.peer["up"] else (who + "連不到")
        offload = ""
        if remote.is_enabled() and ha.peer["up"]:
            offload = "，畫圖暫停分擔改本機" if remote._down_until > time.monotonic() else "，畫圖交給對方"
        blocks.append(f"**對方主機**\n{state}{offload}\n持續 {_duration(now - ha.peer['since'])}"
                      f"　上次檢查 {int(now - ha.peer['checked'])} 秒前")
    return blocks


class OwnerView(PanelView):
    """A back office page: the normal panel checks, plus the OWNER_IDS check on every click."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await super().interaction_check(interaction) and await require_bot_owner(interaction)

    async def back_to_backend(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer()
        await self.go(interaction, await BackendHomeView.create(self.bot, self.opener))


class OwnerModal(PanelModal):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await require_bot_owner(interaction)


# --------------------------------------------------------------------------- home
class BackendHomeView(OwnerView):
    @classmethod
    async def create(cls, bot, opener: discord.Interaction) -> "BackendHomeView":
        view = cls(bot, opener)
        view.heading("後臺", "機器人擁有者專用")
        for block in await status_blocks(bot):
            view.text(block)
        view.divider()
        view.item("伺服器", "查看所在的伺服器，離開或封鎖", "開啟", view._servers, style=PRIMARY)
        view.item("封鎖名單", "查看、新增、解除封鎖", "開啟", view._blocklist)
        view.item("狀態圖", "畫出最近一段時間的折線圖", "查看", view._chart)
        view.item("讓出主機", "暫時把 Discord 連線交給另一台", "設定", view._handover)
        view.item("載入圖片", "更換「正在產生歡迎圖片」的圖", "開啟", view._loading)
        view.item("同步指令", "強制重新登錄斜線指令", "同步", view._sync)
        view.item("清除快取", "清掉記憶體裡的設定快取", "清除", view._clear_cache)
        view.divider()
        view.buttons(make_button("重新整理", view._refresh), make_button("返回", view._back))
        view.footer()
        return view

    async def _servers(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        browser = ServerBrowser(self.bot, self.opener)
        await browser.render()
        await self.go(interaction, browser)

    async def _blocklist(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.go(interaction, await BlockView.create(self.bot, self.opener))

    async def _chart(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ChartModal(self.bot))

    async def _handover(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(HandoverModal())

    async def _loading(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.go(interaction, await LoadingView.create(self.bot, self.opener))

    async def _sync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            count, _ = await cmdsync.sync(self.bot, force=True)
        except discord.HTTPException as exc:
            await interaction.followup.send(f"同步失敗（{exc.status}），請稍後再試。", ephemeral=True)
            return
        await interaction.followup.send(f"已同步 {count} 個指令。可能需要重開 Discord 才看得到。", ephemeral=True)

    async def _clear_cache(self, interaction: discord.Interaction) -> None:
        before = database.cache_size()
        database.clear_cache()
        await interaction.response.send_message(f"已清除 {before} 筆快取（下次使用時重新讀取資料庫）。", ephemeral=True)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.go(interaction, await BackendHomeView.create(self.bot, self.opener))

    async def _back(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await go_home(self, interaction)


# ------------------------------------------------------------------ status chart
class ChartModal(OwnerModal):
    def __init__(self, bot) -> None:
        super().__init__("狀態圖")
        self.bot = bot
        self.minutes = discord.ui.TextInput(default="60", required=True, max_length=4, placeholder="10 到 1440")
        self.add_item(discord.ui.Label(text="要看最近幾分鐘", description="10 到 1440", component=self.minutes))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = self.minutes.value.strip()
        if not text.isdigit() or not 10 <= int(text) <= 1440:
            await reject(interaction, "請輸入 10 到 1440 之間的數字。")
            return
        minutes = int(text)
        monitor = self.bot.get_cog("Monitor")
        if monitor is None or not config.MONITOR_ENABLED:
            await reject(interaction, "狀態監控目前是關閉的（環境變數 MONITOR=false）。")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        png = await monitor.chart_png(minutes)
        if png is None:
            await interaction.followup.send("資料還不夠，每 30 秒記錄一次，請稍等一分鐘再試。", ephemeral=True)
            return
        await interaction.followup.send(
            f"最近 {minutes} 分鐘（共 {len(monitor.samples)} 筆紀錄，保留 {config.METRIC_RETENTION_HOURS} 小時）",
            file=discord.File(io.BytesIO(png), filename="status.png"), ephemeral=True)


# ------------------------------------------------------------------------ handover
class ConfirmView(discord.ui.LayoutView):
    """A small private message with confirm and cancel buttons for one risky action."""

    def __init__(self, user_id: int, question: str, done_text: str, after: Callable[[], None]) -> None:
        super().__init__(timeout=60)
        self.user_id, self.done_text, self.after = user_id, done_text, after
        confirm = make_button("確認", self._confirm, style=DANGER)
        cancel = make_button("取消", self._cancel)
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(question), discord.ui.ActionRow(confirm, cancel),
                                           accent_colour=config.WARNING_COLOR))

    @staticmethod
    def _plain(text: str) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(discord.ui.TextDisplay(text), accent_colour=config.WELCOME_COLOR))
        return view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id and await require_bot_owner(interaction)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(view=self._plain(self.done_text))
        self.stop()
        self.after()  # runs after the reply is sent, because it may disconnect this very bot

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(view=self._plain("已取消。"))
        self.stop()


class HandoverModal(OwnerModal):
    def __init__(self) -> None:
        super().__init__("讓出主機")
        self.minutes = discord.ui.TextInput(default="2", required=True, max_length=2, placeholder="1 到 60")
        self.add_item(discord.ui.Label(text="讓出幾分鐘", description="這台主機會暫時離線，讓另一台接手",
                                       component=self.minutes))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = self.minutes.value.strip()
        if not text.isdigit() or not 1 <= int(text) <= 60:
            await reject(interaction, "請輸入 1 到 60 之間的數字。")
            return
        minutes = int(text)
        supervisor = ha.active_supervisor
        if supervisor is None:
            await reject(interaction, "目前不是雙主機備援模式（需要設定 Turso 並在兩台主機都執行機器人）。")
            return
        view = ConfirmView(
            interaction.user.id,
            f"這台主機（{config.NODE_NAME}）會中斷 Discord 連線約 {minutes} 分鐘，讓另一台接手。"
            "交接期間可能有一小段時間沒有機器人在線。確定嗎？",
            f"這台主機會在幾秒內離線 {minutes} 分鐘，另一台主機接手。",
            lambda: supervisor.request_handover(minutes * 60))
        await interaction.response.send_message(view=view, ephemeral=True)


# ------------------------------------------------------------------- server list
class GuildSelect(discord.ui.Select):
    def __init__(self, guilds: list) -> None:
        options = [discord.SelectOption(label=short(guild.name, 100) or str(guild.id), value=str(guild.id),
                                        description=f"{guild.member_count or 0} 位成員 · ID {guild.id}")
                   for guild in guilds]
        super().__init__(placeholder="選擇一個伺服器", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.change(interaction, state="detail", guild_id=int(self.values[0]))


class KeywordModal(OwnerModal):
    def __init__(self, browser: "ServerBrowser") -> None:
        super().__init__("搜尋伺服器")
        self.browser = browser
        self.keyword = discord.ui.TextInput(default=browser.keyword, required=False, max_length=100,
                                            placeholder="伺服器名稱或 ID，留空顯示全部")
        self.add_item(discord.ui.Label(text="關鍵字", component=self.keyword))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.browser.change(interaction, keyword=self.keyword.value.strip(), page=0, state="list")


class ServerBrowser(OwnerView):
    """Server list -> server details -> leave (with confirmation). One page that redraws itself."""

    def __init__(self, bot, opener: discord.Interaction) -> None:
        super().__init__(bot, opener)
        self.keyword, self.state, self.page, self.guild_id, self.mode, self.result = "", "list", 0, None, "", ""

    async def change(self, interaction: discord.Interaction, **changes) -> None:
        for key, value in changes.items():
            setattr(self, key, value)
        if not interaction.response.is_done():
            await interaction.response.defer()
        await self.render()
        await interaction.edit_original_response(view=self)

    def _goto(self, **changes):
        async def action(interaction: discord.Interaction) -> None:
            await self.change(interaction, **changes)
        return action

    async def _confirm(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.result = await leave_guild(self.bot, self.guild_id, self.mode)
        await self.change(interaction, state="done")

    async def _search(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(KeywordModal(self))

    def _guilds(self) -> list:
        guilds = sorted(self.bot.guilds, key=lambda g: (-(g.member_count or 0), g.name.lower()))
        if self.keyword:
            key = self.keyword.lower()
            guilds = [g for g in guilds if key in g.name.lower() or key == str(g.id)]
        return guilds

    async def render(self) -> None:
        self.reset()
        if self.state in ("detail", "confirm") and self.bot.get_guild(self.guild_id) is None:
            self.state = "list"          # it was left or removed in the meantime
        if self.state == "detail":
            await self._detail()
        elif self.state == "confirm":
            self._confirm_page()
        elif self.state == "done":
            self.heading("完成", self.result)
            self.divider()
            self.buttons(make_button("返回列表", self._goto(state="list")))
        else:
            self._list()
        self.footer()

    def _list(self) -> None:
        guilds = self._guilds()
        pages = max(1, -(-len(guilds) // PAGE_SIZE))
        self.page = max(0, min(self.page, pages - 1))
        chunk = guilds[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        lines = [f"`{n}.` **{short(g.name, 40)}**　{g.member_count or 0} 人　`{g.id}`"
                 for n, g in enumerate(chunk, start=self.page * PAGE_SIZE + 1)]
        self.heading(f"伺服器列表（共 {len(guilds)} 個）",
                     (f"篩選：{short(self.keyword, 30)}\n" if self.keyword else "") + f"第 {self.page + 1}/{pages} 頁")
        self.divider()
        self.text("\n".join(lines) or "沒有符合的伺服器。")
        if chunk:
            self.select(GuildSelect(chunk))
        self.divider()
        self.buttons(make_button("上一頁", self._goto(page=self.page - 1), disabled=self.page == 0),
                     make_button("下一頁", self._goto(page=self.page + 1), disabled=self.page >= pages - 1),
                     make_button("搜尋", self._search), make_button("重新整理", self._goto()),
                     make_button("返回後臺", self.back_to_backend))

    async def _detail(self) -> None:
        guild = self.bot.get_guild(self.guild_id)
        self.heading(short(guild.name, 80), f"ID `{guild.id}`")
        self.divider()
        self.text(f"**基本資料**\n擁有者：<@{guild.owner_id}>　成員：{guild.member_count or 0}\n"
                  f"建立：{discord.utils.format_dt(guild.created_at, 'D')}"
                  f"　文字頻道：{len(guild.text_channels)}　身分組：{len(guild.roles)}")
        me = guild.me
        if me is not None:
            perms = me.guild_permissions
            joined = discord.utils.format_dt(me.joined_at, "R") if me.joined_at else "未知"
            self.text(f"**機器人在這裡**\n加入：{joined}\n管理員：{'是' if perms.administrator else '否'}"
                      f"　管理身分組：{'是' if perms.manage_roles else '否'}"
                      f"　附加檔案：{'是' if perms.attach_files else '否'}")
        try:
            welcome = await database.peek_settings("welcome", guild.id)
            leave = await database.peek_settings("leave", guild.id)
            autorole = await database.peek_settings("autorole", guild.id)
            access = await database.get_access(guild.id)
            auto = "未設定" if autorole is None else ("啟用" if autorole["enabled"] and autorole["role_id"] else "停用")
            self.text(f"**設定**\n歡迎：{_feature(welcome)}\n離開：{_feature(leave)}\n自動身分組：{auto}\n"
                      f"權限名單：{len(access['user'])} 位成員、{len(access['role'])} 個身分組")
            if guild.id in await database.get_blocked():
                self.text("**注意**\n這個伺服器在封鎖名單裡。")
        except database.DatabaseError:
            self.text("**設定**\n目前無法讀取資料庫。")
        self.divider()
        self.buttons(make_button("離開伺服器", self._goto(state="confirm", mode="leave"), style=DANGER),
                     make_button("離開並清除設定", self._goto(state="confirm", mode="wipe"), style=DANGER),
                     make_button("離開並封鎖", self._goto(state="confirm", mode="block"), style=DANGER),
                     make_button("返回列表", self._goto(state="list")))

    def _confirm_page(self) -> None:
        guild = self.bot.get_guild(self.guild_id)
        what = {"leave": "只是離開。設定會保留，之後重新邀請就能繼續使用。",
                "wipe": "離開，並刪除這個伺服器在資料庫裡的所有設定與自訂背景。",
                "block": "離開、刪除所有設定，並加入封鎖名單（之後再被邀請會自動離開）。"}[self.mode]
        self.reset(config.WARNING_COLOR)
        self.heading("確定要離開嗎", f"**{short(guild.name, 60)}**（`{guild.id}`，{guild.member_count or 0} 位成員）")
        self.text(what)
        self.divider()
        self.buttons(make_button("確認", self._confirm, style=DANGER), make_button("取消", self._goto(state="detail")))


# --------------------------------------------------------------------- block list
class BlockModal(OwnerModal):
    def __init__(self, page: "BlockView") -> None:
        super().__init__("封鎖伺服器")
        self.page = page
        self.server_id = discord.ui.TextInput(required=True, max_length=20, placeholder="15 到 20 位數字")
        self.add_item(discord.ui.Label(text="伺服器 ID", description="目前在裡面就離開，之後被邀請也會自動離開",
                                       component=self.server_id))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild_id = _parse_guild_id(self.server_id.value)
        if guild_id is None:
            await reject(interaction, "請輸入正確的伺服器 ID（15 到 20 位數字）。")
            return
        await interaction.response.defer()
        guild = self.page.bot.get_guild(guild_id)
        result = await database.add_blocked(guild_id, guild.name if guild else "（未知）")
        note = {"added": f"已封鎖 `{guild_id}`。", "exists": f"`{guild_id}` 已經在封鎖名單裡。",
                "full": "封鎖名單已滿。"}[result]
        if guild is not None and result != "full":
            note += "\n" + await leave_guild(self.page.bot, guild_id, "leave")
        self.page.stop()
        await show(interaction, await BlockView.create(self.page.bot, self.page.opener))
        await interaction.followup.send(note, ephemeral=True)


class BlockView(OwnerView):
    @classmethod
    async def create(cls, bot, opener: discord.Interaction) -> "BlockView":
        view = cls(bot, opener)
        blocked = await database.get_blocked()
        shown = list(blocked.items())[:PAGE_SIZE]
        lines = [f"`{gid}`　{short(name, 40)}" for gid, name in shown]
        more = f"\n還有 {len(blocked) - len(shown)} 個" if len(blocked) > len(shown) else ""
        view.heading(f"封鎖名單（{len(blocked)}）", "被封鎖的伺服器邀請機器人時，機器人會自動離開")
        view.divider()
        view.text(("\n".join(lines) + more) if lines else "封鎖名單是空的。")
        if shown:
            view.menu = discord.ui.Select(placeholder="選擇要解除封鎖的伺服器", options=[
                discord.SelectOption(label=short(name, 100) or str(gid), value=str(gid), description=f"ID {gid}")
                for gid, name in shown])
            view.menu.callback = view._unblock
            view.select(view.menu)
        view.divider()
        view.buttons(make_button("封鎖伺服器", view._block, style=DANGER), make_button("返回後臺", view.back_to_backend))
        view.footer()
        return view

    async def _block(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(BlockModal(self))

    async def _unblock(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await database.remove_blocked(int(self.menu.values[0]))
        await self.go(interaction, await BlockView.create(self.bot, self.opener))


# ------------------------------------------------------------------ loading image
class LoadingModal(OwnerModal):
    def __init__(self, page: "LoadingView") -> None:
        super().__init__("更換載入圖片")
        self.page = page
        self.upload = discord.ui.FileUpload(required=True, min_values=1, max_values=1)
        self.add_item(discord.ui.Label(
            text="新的圖片", description=f"PNG、JPG、WebP 或 GIF，{config.MAX_BACKGROUND_BYTES // 1024 // 1024}MB 以內",
            component=self.upload))
        self.motion = discord.ui.Select(options=[
            discord.SelectOption(label=label, value=key, default=key == "drift") for key, label in MOTIONS.items()])
        self.add_item(discord.ui.Label(text="單張圖片的動畫", component=self.motion))
        self.transparent = discord.ui.Select(options=[
            discord.SelectOption(label="把深色背景變成透明", value="yes", default=True),
            discord.SelectOption(label="保留原本的背景", value="no")])
        self.add_item(discord.ui.Label(text="背景", component=self.transparent))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        attachment = self.upload.values[0]
        if attachment.size > config.MAX_BACKGROUND_BYTES:
            await reject(interaction, f"圖片超過 {config.MAX_BACKGROUND_BYTES // 1024 // 1024}MB。")
            return
        await interaction.response.defer()
        try:
            data = await attachment.read()
            async with imaging.RENDER_LOCK:
                gif = await asyncio.to_thread(imaging.process_loading, data, transparent=self.transparent.values[0] == "yes",
                                              motion=self.motion.values[0])
            del data
            await database.set_asset("loading", gif)
        except imaging.BackgroundError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.followup.send("無法下載附件，請稍後再試。", ephemeral=True)
            return
        self.page.stop()
        await show(interaction, await LoadingView.create(self.page.bot, self.page.opener))
        await self.page.preview(interaction, f"已更新（{len(gif) // 1024} KB，存在資料庫，重啟或換主機都不會消失）。")


class LoadingView(OwnerView):
    @classmethod
    async def create(cls, bot, opener: discord.Interaction) -> "LoadingView":
        view = cls(bot, opener)
        custom = await database.get_asset("loading")
        view.has_custom = custom is not None
        view.heading("載入圖片", "「正在產生歡迎圖片」訊息旁邊的透明 GIF")
        view.divider()
        view.item("目前使用", f"自訂圖片（{len(custom) // 1024} KB）" if custom else "內建預設圖", "上傳新圖片",
                  view._upload, style=PRIMARY)
        view.item("預覽", "看目前使用的圖片", "預覽", view._preview_click)
        view.item("還原預設", "改回內建的預設圖", "還原", view._reset, disabled=not view.has_custom)
        view.divider()
        view.buttons(make_button("返回後臺", view.back_to_backend))
        view.footer()
        return view

    async def preview(self, interaction: discord.Interaction, text: str) -> None:
        file = await assets_store.loading_file()
        if file:
            await interaction.followup.send(text + "\n下面是預覽，背景是透明的。", file=file, ephemeral=True)
        else:
            await interaction.followup.send(text, ephemeral=True)

    async def _upload(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(LoadingModal(self))

    async def _preview_click(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.preview(interaction, "目前使用的載入圖片：")

    async def _reset(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await database.delete_asset("loading")
        await self.go(interaction, await LoadingView.create(self.bot, self.opener))
        await interaction.followup.send("已還原成內建的預設圖。", ephemeral=True)
