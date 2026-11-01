"""Welcome system: on_member_join listener and the /歡迎 commands."""
import asyncio
import io
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import database
from utils import image as imaging
from utils import remote
import busywork
from utils.formatter import VARIABLES_DESCRIPTION, VARIABLES_HELP, clean_template, render_message
from utils.messaging import (STYLE_CHOICES, AdminGroup, build_message_kwargs, channel_warnings,
                             deliver, reply)

log = logging.getLogger(__name__)

MESSAGE_HELP = "訊息內容，可用變數：" + VARIABLES_HELP
MessageText = app_commands.Range[str, 1, config.MAX_MESSAGE_LENGTH]
THEME_CHOICES = [app_commands.Choice(name=info["label"], value=key) for key, info in imaging.THEMES.items()]
BACKGROUND_CHOICES = [app_commands.Choice(name="純色（簡約線條）", value="plain"),
                      app_commands.Choice(name="預設橫幅（黑白）", value="banner")]


class WelcomeCog(commands.Cog, name="Welcome"):
    welcome = AdminGroup(name="歡迎", description="設定歡迎系統")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ events
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        try:
            settings = await database.get_settings("welcome", member.guild.id)
            if not settings["enabled"] or not settings["channel_id"]:
                return
            sent, notes = await self._send_welcome(member, settings)
            if not sent or notes:
                log.warning("Welcome in guild %s: sent=%s notes=%s", member.guild.id, sent, notes)
        except database.DatabaseError:
            log.exception("Database error while welcoming a member in guild %s", member.guild.id)
        except Exception:
            log.exception("Unexpected error while welcoming a member in guild %s", member.guild.id)

    # ----------------------------------------------------------------- helpers
    async def _send_welcome(self, member: discord.Member, settings: dict) -> tuple[bool, list[str]]:
        text = render_message(settings["message"], member, member.guild)
        make_image = (lambda: self._make_image(member, settings)) if settings["image_enabled"] else None
        return await deliver(
            bot=self.bot, guild=member.guild, channel_id=settings["channel_id"], user=member,
            text=text, use_embed=bool(settings["use_embed"]), colour=imaging.theme_embed_colour(settings["theme"]),
            make_image=make_image, ping_user=True,
        )

    async def _make_image(self, member: discord.Member, settings: dict) -> Optional[discord.File]:
        """Render the welcome image. Returns None on any failure instead of raising."""
        try:
            avatar_bytes = None
            try:
                avatar_bytes = await member.display_avatar.replace(size=256, format="png").read()
            except discord.DiscordException:
                log.warning("Could not download the avatar of %s", member.id)

            background, banner = None, False
            if settings["background_path"] == "builtin":          # the banner that ships with the bot
                background, banner = config.DEFAULT_BACKGROUND, True
            elif settings["background_path"]:                      # an image uploaded by the admins
                candidate = config.DATA_DIR / settings["background_path"]
                background = candidate if candidate.is_file() else None
            theme = settings["theme"]

            buffer = None
            if remote.is_enabled():  # offload the drawing to the image worker if one is configured
                png = await remote.render(
                    avatar_bytes, member.display_name, member.guild.name, member.guild.member_count or 0,
                    background.read_bytes() if background and not banner else None, member.name,
                    banner=banner, theme=theme)
                if png is not None:
                    buffer = io.BytesIO(png)

            if buffer is None:  # no worker, or it failed: draw locally, one image at a time
                async with imaging.RENDER_LOCK:  # one image at a time, shared with the other host's requests
                    with busywork.busy():
                        buffer = await asyncio.to_thread(
                            imaging.build_welcome_image, avatar_bytes, member.display_name,
                            member.guild.name, member.guild.member_count or 0, background, member.name, theme,
                        )
            return discord.File(buffer, filename="welcome.png")
        except Exception:
            log.exception("Welcome image generation failed in guild %s", member.guild.id)
            return None

    # --------------------------------------------------------------- commands
    @welcome.command(name="快速設定", description="啟用歡迎系統並選擇頻道")
    @app_commands.describe(channel="發送歡迎訊息的頻道", message=MESSAGE_HELP,
                           style="純文字或嵌入式訊息", image="是否附上歡迎圖片")
    @app_commands.rename(channel="頻道", message="訊息", style="樣式", image="圖片")
    @app_commands.choices(style=STYLE_CHOICES)
    async def welcome_setup(self, interaction: discord.Interaction, channel: discord.TextChannel,
                            message: Optional[MessageText] = None,
                            style: Optional[app_commands.Choice[str]] = None,
                            image: Optional[bool] = None) -> None:
        fields: dict = {"enabled": 1, "channel_id": channel.id}
        if message:
            fields["message"] = clean_template(message)
        if style:
            fields["use_embed"] = int(style.value == "embed")
        if image is not None:
            fields["image_enabled"] = int(image)
        await database.update_settings("welcome", interaction.guild_id, **fields)

        settings = await database.get_settings("welcome", interaction.guild_id)
        notes = await channel_warnings(self.bot, channel, embeds=bool(settings["use_embed"]),
                                       files=bool(settings["image_enabled"]))
        text = f"✅ 已在 {channel.mention} 啟用歡迎訊息。"
        if notes:
            text += "\n" + "\n".join(f"⚠️ {note}" for note in notes)
        text += "\n可以用 `/歡迎 測試` 或 `/歡迎 預覽` 看看效果。"
        await reply(interaction, text)

    @welcome.command(name="啟用", description="啟用歡迎系統")
    async def welcome_enable(self, interaction: discord.Interaction) -> None:
        settings = await database.get_settings("welcome", interaction.guild_id)
        await database.update_settings("welcome", interaction.guild_id, enabled=1)
        if not settings["channel_id"]:
            await reply(interaction, "✅ 已啟用，但還沒有設定頻道，請用 `/歡迎 頻道` 選擇。")
        else:
            await reply(interaction, "✅ 歡迎系統已啟用。")

    @welcome.command(name="停用", description="停用歡迎系統")
    async def welcome_disable(self, interaction: discord.Interaction) -> None:
        await database.update_settings("welcome", interaction.guild_id, enabled=0)
        await reply(interaction, "✅ 歡迎系統已停用，設定會保留。")

    @welcome.command(name="頻道", description="選擇歡迎頻道")
    @app_commands.describe(channel="發送歡迎訊息的頻道")
    @app_commands.rename(channel="頻道")
    async def welcome_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await database.update_settings("welcome", interaction.guild_id, channel_id=channel.id)
        settings = await database.get_settings("welcome", interaction.guild_id)
        notes = await channel_warnings(self.bot, channel, embeds=bool(settings["use_embed"]),
                                       files=bool(settings["image_enabled"]))
        text = f"✅ 歡迎訊息會發送到 {channel.mention}。"
        if notes:
            text += "\n" + "\n".join(f"⚠️ {note}" for note in notes)
        await reply(interaction, text)

    @welcome.command(name="訊息", description="設定歡迎訊息內容與樣式（不填則顯示目前設定）")
    @app_commands.describe(text=MESSAGE_HELP, style="純文字或嵌入式訊息")
    @app_commands.rename(text="內容", style="樣式")
    @app_commands.choices(style=STYLE_CHOICES)
    async def welcome_message(self, interaction: discord.Interaction, text: Optional[MessageText] = None,
                              style: Optional[app_commands.Choice[str]] = None) -> None:
        if text is None and style is None:
            settings = await database.get_settings("welcome", interaction.guild_id)
            kind = "嵌入式訊息" if settings["use_embed"] else "純文字"
            await reply(interaction, f"目前樣式：**{kind}**\n目前內容：\n```{settings['message']}```\n"
                                     f"可用變數：`{VARIABLES_HELP}`\n{VARIABLES_DESCRIPTION}\n輸入 `\\n` 可以換行。")
            return
        fields: dict = {}
        if text is not None:
            fields["message"] = clean_template(text)
        if style is not None:
            fields["use_embed"] = int(style.value == "embed")
        await database.update_settings("welcome", interaction.guild_id, **fields)
        await reply(interaction, "✅ 歡迎訊息已更新，可以用 `/歡迎 預覽` 查看。")

    @welcome.command(name="圖片", description="設定歡迎圖片的主題與背景（不填任何選項則顯示目前設定）")
    @app_commands.describe(enabled="開啟或關閉歡迎圖片", theme="黑白色系：黑色、深灰、白色、淺灰",
                           background_style="純色線條，或套用預設橫幅（會轉成黑白）",
                           background="自訂背景圖（PNG 或 JPG，5MB 以內，會轉成黑白）",
                           reset="還原成預設（黑色、純色）")
    @app_commands.rename(enabled="啟用", theme="主題", background_style="背景樣式", background="背景",
                         reset="還原預設")
    @app_commands.choices(theme=THEME_CHOICES, background_style=BACKGROUND_CHOICES)
    async def welcome_image(self, interaction: discord.Interaction, enabled: Optional[bool] = None,
                            theme: Optional[app_commands.Choice[str]] = None,
                            background_style: Optional[app_commands.Choice[str]] = None,
                            background: Optional[discord.Attachment] = None,
                            reset: Optional[bool] = None) -> None:
        guild_id = interaction.guild_id
        settings = await database.get_settings("welcome", guild_id)

        if enabled is None and theme is None and background_style is None and background is None and not reset:
            path = settings["background_path"]
            kind = "預設橫幅" if path == "builtin" else ("自訂圖片" if path else "純色")
            await reply(interaction,
                        f"歡迎圖片：**{'開啟' if settings['image_enabled'] else '關閉'}**\n"
                        f"主題：**{imaging.get_theme(settings['theme'])['label']}**\n背景：**{kind}**")
            return

        await interaction.response.defer(ephemeral=True)
        fields: dict = {}
        destination = config.CUSTOM_BACKGROUND_DIR / f"{guild_id}.jpg"

        def drop_custom_file() -> None:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                log.warning("Could not delete %s", destination)

        if reset:
            fields.update(background_path=None, theme=imaging.DEFAULT_THEME)
            drop_custom_file()

        if background_style is not None:
            fields["background_path"] = "builtin" if background_style.value == "banner" else None
            drop_custom_file()

        if background is not None:
            content_type = (background.content_type or "").lower()
            looks_valid = content_type in {"image/png", "image/jpeg"} or \
                background.filename.lower().endswith((".png", ".jpg", ".jpeg"))
            if not looks_valid:
                await reply(interaction, "❌ 請上傳 PNG 或 JPG 圖片。")
                return
            if background.size > config.MAX_BACKGROUND_BYTES:
                await reply(interaction, "❌ 圖片超過 5MB。")
                return
            try:
                data = await background.read()
                async with imaging.RENDER_LOCK:
                    await asyncio.to_thread(imaging.save_custom_background, data, destination)
            except imaging.BackgroundError as exc:
                await reply(interaction, f"❌ {exc}")
                return
            except discord.HTTPException:
                await reply(interaction, "❌ 無法下載附件，請稍後再試。")
                return
            fields["background_path"] = f"backgrounds/{guild_id}.jpg"

        if theme is not None:
            fields["theme"] = theme.value
        if enabled is not None:
            fields["image_enabled"] = int(enabled)

        await database.update_settings("welcome", guild_id, **fields)
        await reply(interaction, "✅ 歡迎圖片設定已更新，可以用 `/歡迎 預覽` 查看。")

    @welcome.command(name="測試", description="把測試歡迎訊息發送到歡迎頻道")
    async def welcome_test(self, interaction: discord.Interaction) -> None:
        settings = await database.get_settings("welcome", interaction.guild_id)
        if not settings["channel_id"]:
            await reply(interaction, "❌ 還沒有設定歡迎頻道，請先用 `/歡迎 頻道`。")
            return
        await interaction.response.defer(ephemeral=True)
        sent, notes = await self._send_welcome(interaction.user, settings)
        text = f"✅ 測試訊息已發送到 <#{settings['channel_id']}>。" if sent else "❌ 測試訊息發送失敗。"
        if notes:
            text += "\n" + "\n".join(f"⚠️ {note}" for note in notes)
        await reply(interaction, text)

    @welcome.command(name="預覽", description="在這裡預覽歡迎訊息（只有你看得到）")
    async def welcome_preview(self, interaction: discord.Interaction) -> None:
        settings = await database.get_settings("welcome", interaction.guild_id)
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        text = render_message(settings["message"], member, interaction.guild)
        file = await self._make_image(member, settings) if settings["image_enabled"] else None
        kwargs = build_message_kwargs(member, text, bool(settings["use_embed"]),
                                      imaging.theme_embed_colour(settings["theme"]), file)
        await interaction.followup.send(ephemeral=True, **kwargs)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))
