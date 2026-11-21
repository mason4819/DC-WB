"""Welcome page of the /設定 panel (and its background sub-page)."""
import asyncio
import io
import logging

import discord

import config
import database
from panels.base import PanelModal, PanelView, go_home, make_embed, reject, show
from panels.common import MessageModal, message_field, style_label
from utils import assets_store
from utils import image as imaging
from utils.formatter import render_message
from utils.messaging import build_message_kwargs, channel_warnings

log = logging.getLogger(__name__)

DELIVERY_LABELS = {"channel": "發送到頻道", "dm": "私訊給新成員", "both": "頻道和私訊都要"}
TEXT_CHANNELS = [discord.ChannelType.text, discord.ChannelType.news]


def background_label(settings: dict) -> str:
    path = settings["background_path"]
    return "預設橫幅" if path == "builtin" else ("自訂圖片" if path else "純色")


class WelcomeView(PanelView):
    @classmethod
    async def create(cls, bot, opener: discord.Interaction) -> "WelcomeView":
        view = cls(bot, opener)
        view.settings = await database.get_settings("welcome", opener.guild_id)
        view.embed = await view._build_embed()
        view._build_items()
        return view

    async def _build_embed(self) -> discord.Embed:
        s, guild = self.settings, self.opener.guild
        embed = make_embed("🎉 歡迎系統", "✅ 已啟用" if s["enabled"] else "❌ 已停用")
        embed.colour = imaging.theme_embed_colour(s["theme"])
        embed.add_field(name="發送方式", value=DELIVERY_LABELS[s["delivery"]])
        embed.add_field(name="頻道", value=f"<#{s['channel_id']}>" if s["channel_id"] else "尚未設定")
        embed.add_field(name="訊息樣式", value=style_label(s["use_embed"]))
        embed.add_field(name="歡迎圖片", value=("開啟" if s["image_enabled"] else "關閉") +
                        f"・{imaging.get_theme(s['theme'])['label']}・{background_label(s)}", inline=False)
        embed.add_field(name="訊息內容", value=message_field(s["message"]), inline=False)

        notes: list[str] = []
        if s["delivery"] in ("channel", "both"):
            if not s["channel_id"]:
                notes.append("還沒有選擇歡迎頻道，頻道這部分不會發送。")
            else:
                notes += await channel_warnings(self.bot, guild.get_channel(s["channel_id"]),
                                                embeds=bool(s["use_embed"]), files=bool(s["image_enabled"]))
        if s["delivery"] in ("dm", "both"):
            notes.append("私訊：成員可以關閉「允許伺服器成員私訊」，這時私訊會失敗，不影響其他功能。")
        if notes:
            embed.add_field(name="⚠️ 提醒", value="\n".join(f"• {n}" for n in notes)[:1000], inline=False)
        return embed

    def _build_items(self) -> None:
        s, guild = self.settings, self.opener.guild
        self.add_button("停用" if s["enabled"] else "啟用", self._toggle("enabled"), row=0,
                        style=discord.ButtonStyle.danger if s["enabled"] else discord.ButtonStyle.success)
        self.add_button("改成純文字" if s["use_embed"] else "改成嵌入式", self._toggle("use_embed"), row=0)
        self.add_button("關閉圖片" if s["image_enabled"] else "開啟圖片", self._toggle("image_enabled"), row=0)
        self.add_button("編輯訊息內容", self._edit_message, row=0, emoji="✏️", style=discord.ButtonStyle.primary)

        channel = guild.get_channel(s["channel_id"]) if s["channel_id"] else None
        menu = discord.ui.ChannelSelect(channel_types=TEXT_CHANNELS, placeholder="選擇歡迎頻道", row=1,
                                        default_values=[channel] if channel is not None else [])
        menu.callback = self._pick_channel
        self.channel_menu = menu
        self.add_item(menu)

        delivery = discord.ui.Select(placeholder="發送方式", row=2, options=[
            discord.SelectOption(label=label, value=key, default=key == s["delivery"])
            for key, label in DELIVERY_LABELS.items()])
        delivery.callback = self._pick_delivery
        self.delivery_menu = delivery
        self.add_item(delivery)

        theme = discord.ui.Select(placeholder="圖片主題", row=3, options=[
            discord.SelectOption(label=info["label"], value=key, default=key == s["theme"])
            for key, info in imaging.THEMES.items()])
        theme.callback = self._pick_theme
        self.theme_menu = theme
        self.add_item(theme)

        self.add_button("背景", self._background, row=4, emoji="🖼️")
        self.add_button("預覽", self._preview, row=4, emoji="👁️")
        self.add_button("測試", self._test, row=4, emoji="🧪")
        self.add_button("返回", self._back, row=4, emoji="↩️")

    # --------------------------------------------------------------- callbacks
    def _toggle(self, field: str):
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            await database.update_settings("welcome", interaction.guild_id, **{field: 0 if self.settings[field] else 1})
            await self.go(interaction, await WelcomeView.create(self.bot, self.opener))
        return callback

    async def _pick_channel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await database.update_settings("welcome", interaction.guild_id, channel_id=self.channel_menu.values[0].id)
        await self.go(interaction, await WelcomeView.create(self.bot, self.opener))

    async def _pick_delivery(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await database.update_settings("welcome", interaction.guild_id, delivery=self.delivery_menu.values[0])
        await self.go(interaction, await WelcomeView.create(self.bot, self.opener))

    async def _pick_theme(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await database.update_settings("welcome", interaction.guild_id, theme=self.theme_menu.values[0])
        await self.go(interaction, await WelcomeView.create(self.bot, self.opener))

    async def _edit_message(self, interaction: discord.Interaction) -> None:
        async def rebuild() -> PanelView:
            self.stop()
            return await WelcomeView.create(self.bot, self.opener)
        await interaction.response.send_modal(MessageModal("welcome", "編輯歡迎訊息", self.settings["message"], rebuild))

    async def _background(self, interaction: discord.Interaction) -> None:
        await self.go(interaction, await BackgroundView.create(self.bot, self.opener))

    async def _back(self, interaction: discord.Interaction) -> None:
        await go_home(self, interaction)

    async def _preview(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("Welcome")
        await interaction.response.defer(ephemeral=True, thinking=True)
        settings = await database.get_settings("welcome", interaction.guild_id)
        member = interaction.user
        text = render_message(settings["message"], member, interaction.guild)
        file = None
        if settings["image_enabled"]:
            png = await cog.render_image(member, settings)
            file = discord.File(io.BytesIO(png), filename="welcome.png") if png else None
        kwargs = build_message_kwargs(member, text, bool(settings["use_embed"]),
                                      imaging.theme_embed_colour(settings["theme"]), file)
        await interaction.followup.send(ephemeral=True, **kwargs)

    async def _test(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("Welcome")
        settings = await database.get_settings("welcome", interaction.guild_id)
        if settings["delivery"] == "channel" and not settings["channel_id"]:
            await reject(interaction, "❌ 還沒有選擇歡迎頻道，請先在面板上選一個頻道。")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        results = await cog.send_welcome(interaction.user, settings)
        if not results:
            await interaction.followup.send("❌ 沒有可以發送的地方，請先選擇頻道，或把發送方式改成私訊。", ephemeral=True)
            return
        lines: list[str] = []
        for target, (sent, notes) in results.items():
            name = f"頻道 <#{settings['channel_id']}>" if target == "channel" else "私訊（發給你自己）"
            lines.append(f"{'✅' if sent else '❌'} {name}：{'已發送' if sent else '發送失敗'}")
            lines.extend(f"⚠️ {note}" for note in notes)
        await interaction.followup.send("\n".join(lines), ephemeral=True)


class BackgroundUploadModal(PanelModal):
    """Pop-up with a file picker for the custom welcome background."""

    def __init__(self, view: "BackgroundView") -> None:
        super().__init__("上傳自訂背景")
        self.page = view
        self.upload = discord.ui.FileUpload(required=True, min_values=1, max_values=1)
        self.add_item(discord.ui.Label(
            text="背景圖片", description=f"PNG 或 JPG，{config.MAX_BACKGROUND_BYTES // 1024 // 1024}MB 以內，會轉成黑白",
            component=self.upload))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        attachment = self.upload.values[0]
        content_type = (attachment.content_type or "").lower()
        if not (content_type in {"image/png", "image/jpeg"} or attachment.filename.lower().endswith((".png", ".jpg", ".jpeg"))):
            await reject(interaction, "❌ 請上傳 PNG 或 JPG 圖片。")
            return
        if attachment.size > config.MAX_BACKGROUND_BYTES:
            await reject(interaction, f"❌ 圖片超過 {config.MAX_BACKGROUND_BYTES // 1024 // 1024}MB。")
            return
        await interaction.response.defer()
        guild_id = interaction.guild_id
        try:
            data = await attachment.read()
            async with imaging.RENDER_LOCK:
                processed = await asyncio.to_thread(imaging.process_background, data)
            del data
            version = await database.save_background(guild_id, processed)
            assets_store.cache_background(guild_id, version, processed)
        except imaging.BackgroundError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.followup.send("❌ 無法下載附件，請稍後再試。", ephemeral=True)
            return
        except OSError:
            log.warning("Could not cache the new background for guild %s", guild_id, exc_info=True)
        await database.update_settings("welcome", guild_id, background_path=f"db:{version}")
        self.page.stop()
        await show(interaction, await BackgroundView.create(self.page.bot, self.page.opener))


class BackgroundView(PanelView):
    @classmethod
    async def create(cls, bot, opener: discord.Interaction) -> "BackgroundView":
        view = cls(bot, opener)
        view.settings = await database.get_settings("welcome", opener.guild_id)
        path = view.settings["background_path"]
        view.embed = make_embed("🖼️ 歡迎圖片背景", f"目前使用：**{background_label(view.settings)}**")
        view.embed.add_field(name="說明", inline=False, value=(
            "• **純色**：簡約線條，不需要圖片\n• **預設橫幅**：機器人內建的黑白橫幅\n"
            f"• **上傳自訂圖片**：PNG 或 JPG，{config.MAX_BACKGROUND_BYTES // 1024 // 1024}MB 以內，會轉成黑白"))
        view.add_button("純色", view._set_plain, row=0, disabled=path is None)
        view.add_button("預設橫幅", view._set_banner, row=0, disabled=path == "builtin")
        view.add_button("上傳自訂圖片", view._upload, row=0, style=discord.ButtonStyle.primary, emoji="📤")
        view.add_button("返回歡迎設定", view._back, row=1, emoji="↩️")
        return view

    async def _drop_custom(self, guild_id: int) -> None:
        await database.delete_background(guild_id)
        assets_store.drop_background_cache(guild_id)

    async def _set_plain(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self._drop_custom(interaction.guild_id)
        await database.update_settings("welcome", interaction.guild_id, background_path=None)
        await self.go(interaction, await BackgroundView.create(self.bot, self.opener))

    async def _set_banner(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self._drop_custom(interaction.guild_id)
        await database.update_settings("welcome", interaction.guild_id, background_path="builtin")
        await self.go(interaction, await BackgroundView.create(self.bot, self.opener))

    async def _upload(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(BackgroundUploadModal(self))

    async def _back(self, interaction: discord.Interaction) -> None:
        await self.go(interaction, await WelcomeView.create(self.bot, self.opener))
