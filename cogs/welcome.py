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
from utils import assets_store, remote
import busywork
from utils.formatter import VARIABLES_DESCRIPTION, VARIABLES_HELP, clean_template, render_message
from utils.messaging import (STYLE_CHOICES, AdminGroup, build_message_kwargs, channel_warnings,
                             deliver, deliver_dm, reply)

log = logging.getLogger(__name__)

MESSAGE_HELP = "訊息內容，可用變數：" + VARIABLES_HELP
MessageText = app_commands.Range[str, 1, config.MAX_MESSAGE_LENGTH]
DELIVERY_CHOICES = [app_commands.Choice(name="發送到頻道", value="channel"),
                    app_commands.Choice(name="私訊給新成員", value="dm"),
                    app_commands.Choice(name="頻道和私訊都要", value="both")]
DELIVERY_LABELS = {"channel": "發送到頻道", "dm": "私訊給新成員", "both": "頻道和私訊都要"}
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
            if not settings["enabled"]:
                return
            results = await self._send_welcome(member, settings)
            if not results:                       # nothing to do, e.g. channel mode without a channel
                return
            for target, (sent, notes) in results.items():
                if not sent or notes:
                    log.warning("Welcome (%s) in guild %s: sent=%s notes=%s", target, member.guild.id, sent, notes)
        except database.DatabaseError:
            log.exception("Database error while welcoming a member in guild %s", member.guild.id)
        except Exception:
            log.exception("Unexpected error while welcoming a member in guild %s", member.guild.id)

    # ----------------------------------------------------------------- helpers
    async def _send_welcome(self, member: discord.Member, settings: dict) -> dict[str, tuple[bool, list[str]]]:
        """Welcome a member the way the server chose: channel, private message, or both.

        Returns {"channel": (sent, notes), "dm": (sent, notes)} for the targets that were tried.
        The image is drawn once and reused when both are used.
        """
        mode = settings["delivery"]
        use_channel = mode in ("channel", "both") and bool(settings["channel_id"])
        use_dm = mode in ("dm", "both") and not member.bot
        text = render_message(settings["message"], member, member.guild)
        colour = imaging.theme_embed_colour(settings["theme"])
        use_embed = bool(settings["use_embed"])

        cache: dict = {}

        async def make_image() -> Optional[bytes]:
            if "png" not in cache:
                cache["png"] = await self._render_image(member, settings)
            return cache["png"]

        image_factory = make_image if settings["image_enabled"] else None
        results: dict[str, tuple[bool, list[str]]] = {}
        if use_channel:
            results["channel"] = await deliver(
                bot=self.bot, guild=member.guild, channel_id=settings["channel_id"], user=member,
                text=text, use_embed=use_embed, colour=colour, make_image=image_factory, ping_user=True)
        if use_dm:
            results["dm"] = await deliver_dm(user=member, guild=member.guild, text=text, use_embed=use_embed,
                                             colour=colour, make_image=image_factory)
        return results

    async def _render_image(self, member: discord.Member, settings: dict) -> Optional[bytes]:
        """Render the welcome image as PNG bytes. Returns None on any failure instead of raising."""
        try:
            avatar_bytes = None
            try:
                avatar_bytes = await member.display_avatar.replace(size=256, format="png").read()
            except discord.DiscordException:
                log.warning("Could not download the avatar of %s", member.id)

            background, banner = None, False
            if settings["background_path"] == "builtin":          # the banner that ships with the bot
                background, banner = config.DEFAULT_BACKGROUND, True
            elif (settings["background_path"] or "").startswith("db:"):   # uploaded by the admins, kept in the database
                try:
                    background = await assets_store.background_file(member.guild.id, int(settings["background_path"][3:]))
                except (ValueError, database.DatabaseError):
                    log.warning("Could not load the stored background of guild %s", member.guild.id)
            elif settings["background_path"]:                      # old layout: a file on this host's disk
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
            return buffer.getvalue()
        except Exception:
            log.exception("Welcome image generation failed in guild %s", member.guild.id)
            return None

    # --------------------------------------------------------------- commands
    @welcome.command(name="快速設定", description="啟用歡迎系統，選擇頻道與發送方式")
    @app_commands.describe(channel="發送歡迎訊息的頻道（只用私訊時可以不填）", message=MESSAGE_HELP,
                           style="純文字或嵌入式訊息", image="是否附上歡迎圖片",
                           delivery="發送到頻道、私訊給新成員，或兩者都要")
    @app_commands.rename(channel="頻道", message="訊息", style="樣式", image="圖片", delivery="發送方式")
    @app_commands.choices(style=STYLE_CHOICES, delivery=DELIVERY_CHOICES)
    async def welcome_setup(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None,
                            message: Optional[MessageText] = None,
                            style: Optional[app_commands.Choice[str]] = None,
                            image: Optional[bool] = None,
                            delivery: Optional[app_commands.Choice[str]] = None) -> None:
        current = await database.get_settings("welcome", interaction.guild_id)
        mode = delivery.value if delivery else current["delivery"]
        if channel is None and mode in ("channel", "both") and not current["channel_id"]:
            await reply(interaction, "❌ 這個發送方式需要頻道，請填上「頻道」選項（只用私訊的話可以不填）。")
            return
        fields: dict = {"enabled": 1}
        if channel is not None:
            fields["channel_id"] = channel.id
        if delivery:
            fields["delivery"] = delivery.value
        if message:
            fields["message"] = clean_template(message)
        if style:
            fields["use_embed"] = int(style.value == "embed")
        if image is not None:
            fields["image_enabled"] = int(image)
        await database.update_settings("welcome", interaction.guild_id, **fields)

        settings = await database.get_settings("welcome", interaction.guild_id)
        notes: list[str] = []
        if settings["delivery"] in ("channel", "both") and settings["channel_id"]:
            notes = await channel_warnings(self.bot, interaction.guild.get_channel(settings["channel_id"]),
                                           embeds=bool(settings["use_embed"]), files=bool(settings["image_enabled"]))
        text = f"✅ 歡迎系統已啟用，發送方式：**{DELIVERY_LABELS[settings['delivery']]}**。"
        if settings["channel_id"] and settings["delivery"] != "dm":
            text += f"\n頻道：<#{settings['channel_id']}>"
        if notes:
            text += "\n" + "\n".join(f"⚠️ {note}" for note in notes)
        text += "\n可以用 `/歡迎 測試` 或 `/歡迎 預覽` 看看效果。"
        await reply(interaction, text)

    @welcome.command(name="發送方式", description="選擇歡迎訊息發到頻道、私訊給新成員，或兩者都要")
    @app_commands.describe(delivery="發送到頻道、私訊給新成員，或兩者都要")
    @app_commands.rename(delivery="方式")
    @app_commands.choices(delivery=DELIVERY_CHOICES)
    async def welcome_delivery(self, interaction: discord.Interaction, delivery: app_commands.Choice[str]) -> None:
        settings = await database.get_settings("welcome", interaction.guild_id)
        await database.update_settings("welcome", interaction.guild_id, delivery=delivery.value)
        text = f"✅ 發送方式已改成：**{delivery.name}**。"
        if delivery.value in ("channel", "both") and not settings["channel_id"]:
            text += "\n⚠️ 還沒有設定歡迎頻道，頻道這部分不會發送，請用 `/歡迎 頻道` 選擇。"
        if delivery.value in ("dm", "both"):
            text += ("\n私訊的訊息內容與樣式跟頻道相同，結尾會註明來自哪個伺服器。\n"
                     "⚠️ 成員可以關閉「允許伺服器成員私訊」，這時私訊會失敗（不會影響其他功能）；"
                     "短時間大量私訊也可能被 Discord 限制。")
        await reply(interaction, text)

    @welcome.command(name="啟用", description="啟用歡迎系統")
    async def welcome_enable(self, interaction: discord.Interaction) -> None:
        settings = await database.get_settings("welcome", interaction.guild_id)
        await database.update_settings("welcome", interaction.guild_id, enabled=1)
        if settings["delivery"] in ("channel", "both") and not settings["channel_id"]:
            await reply(interaction, "✅ 已啟用，但還沒有設定頻道，請用 `/歡迎 頻道` 選擇。")
        else:
            await reply(interaction, f"✅ 歡迎系統已啟用（{DELIVERY_LABELS[settings['delivery']]}）。")

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
                           background="自訂背景圖（PNG 或 JPG，15MB 以內，會轉成黑白）",
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

        async def drop_stored_background() -> None:
            """Remove the uploaded background from the database and from this host's cache."""
            await database.delete_background(guild_id)
            assets_store.drop_background_cache(guild_id)

        if reset:
            fields.update(background_path=None, theme=imaging.DEFAULT_THEME)
            await drop_stored_background()

        if background_style is not None:
            fields["background_path"] = "builtin" if background_style.value == "banner" else None
            await drop_stored_background()

        if background is not None:
            content_type = (background.content_type or "").lower()
            looks_valid = content_type in {"image/png", "image/jpeg"} or \
                background.filename.lower().endswith((".png", ".jpg", ".jpeg"))
            if not looks_valid:
                await reply(interaction, "❌ 請上傳 PNG 或 JPG 圖片。")
                return
            if background.size > config.MAX_BACKGROUND_BYTES:
                await reply(interaction, f"❌ 圖片超過 {config.MAX_BACKGROUND_BYTES // 1024 // 1024}MB。")
                return
            try:
                data = await background.read()
                async with imaging.RENDER_LOCK:
                    processed = await asyncio.to_thread(imaging.process_background, data)
                del data
                version = await database.save_background(guild_id, processed)   # kept in the database
                assets_store.cache_background(guild_id, version, processed)
            except imaging.BackgroundError as exc:
                await reply(interaction, f"❌ {exc}")
                return
            except discord.HTTPException:
                await reply(interaction, "❌ 無法下載附件，請稍後再試。")
                return
            except OSError:
                log.warning("Could not cache the new background for guild %s", guild_id, exc_info=True)
            fields["background_path"] = f"db:{version}"

        if theme is not None:
            fields["theme"] = theme.value
        if enabled is not None:
            fields["image_enabled"] = int(enabled)

        await database.update_settings("welcome", guild_id, **fields)
        await reply(interaction, "✅ 歡迎圖片設定已更新，可以用 `/歡迎 預覽` 查看。")

    @welcome.command(name="測試", description="依目前的發送方式，把測試歡迎訊息發給你自己（頻道和 / 或私訊）")
    async def welcome_test(self, interaction: discord.Interaction) -> None:
        settings = await database.get_settings("welcome", interaction.guild_id)
        if settings["delivery"] == "channel" and not settings["channel_id"]:
            await reply(interaction, "❌ 還沒有設定歡迎頻道，請先用 `/歡迎 頻道`。")
            return
        await interaction.response.defer(ephemeral=True)
        results = await self._send_welcome(interaction.user, settings)
        if not results:
            await reply(interaction, "❌ 沒有可以發送的地方，請先設定頻道，或把發送方式改成私訊。")
            return
        lines: list[str] = []
        for target, (sent, notes) in results.items():
            name = f"頻道 <#{settings['channel_id']}>" if target == "channel" else "私訊（發給你自己）"
            lines.append(f"{'✅' if sent else '❌'} {name}：{'已發送' if sent else '發送失敗'}")
            lines.extend(f"⚠️ {note}" for note in notes)
        if settings["delivery"] in ("channel", "both") and not settings["channel_id"]:
            lines.append("⚠️ 還沒有設定歡迎頻道，頻道部分沒有發送。")
        await reply(interaction, "\n".join(lines))

    @welcome.command(name="預覽", description="在這裡預覽歡迎訊息（只有你看得到）")
    async def welcome_preview(self, interaction: discord.Interaction) -> None:
        settings = await database.get_settings("welcome", interaction.guild_id)
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        text = render_message(settings["message"], member, interaction.guild)
        file = None
        if settings["image_enabled"]:
            png = await self._render_image(member, settings)
            file = discord.File(io.BytesIO(png), filename="welcome.png") if png else None
        kwargs = build_message_kwargs(member, text, bool(settings["use_embed"]),
                                      imaging.theme_embed_colour(settings["theme"]), file)
        await interaction.followup.send(ephemeral=True, **kwargs)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))
