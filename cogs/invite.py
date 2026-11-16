"""/邀請: post an embed with the bot's invite link straight into the channel.

The slash command is only acknowledged privately and that acknowledgement is deleted again, so
the channel never shows a "<user> used /邀請" line or a reply that later turns into "original
message was deleted". The embed itself is a normal channel message sent by the bot.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

import config

log = logging.getLogger(__name__)

# Permissions the bot actually needs: welcome/leave messages, images, autorole.
INVITE_PERMISSIONS = discord.Permissions(
    view_channel=True, send_messages=True, embed_links=True, attach_files=True,
    manage_roles=True, read_message_history=True,
)


def invite_url(bot: commands.Bot) -> str:
    return discord.utils.oauth_url(bot.user.id, permissions=INVITE_PERMISSIONS,
                                   scopes=("bot", "applications.commands"))


def build_invite(bot: commands.Bot) -> tuple[discord.Embed, discord.ui.View]:
    url = invite_url(bot)
    embed = discord.Embed(
        title="邀請迎賓客到你的伺服器",
        description=(f"{bot.user.name} 是輕量的歡迎機器人：歡迎訊息、離開訊息、歡迎圖片與自動身分組。\n\n"
                     f"**[點此邀請機器人]({url})**\n"
                     f"**[機器人首頁]({config.HOMEPAGE_URL})**"),
        colour=config.INVITE_COLOR,
    )
    if bot.user.display_avatar:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    view = discord.ui.View(timeout=None)          # link buttons need no callbacks, so no state to keep
    view.add_item(discord.ui.Button(label="邀請機器人", style=discord.ButtonStyle.link, url=url))
    view.add_item(discord.ui.Button(label="機器人首頁", style=discord.ButtonStyle.link, url=config.HOMEPAGE_URL))
    return embed, view


class InviteCog(commands.Cog, name="Invite"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="邀請", description="在這個頻道發送機器人的邀請連結與首頁")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, config.INVITE_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
    async def invite(self, interaction: discord.Interaction) -> None:
        # Acknowledge privately (nobody else sees it), then remove it again after the real message is out.
        await interaction.response.defer(ephemeral=True)
        embed, view = build_invite(self.bot)

        channel = interaction.channel
        me = interaction.guild.me if interaction.guild else None
        perms = channel.permissions_for(me) if (channel is not None and me is not None) else None
        if perms is not None and not (perms.view_channel and perms.send_messages and perms.embed_links):
            # Cannot post here: tell only the caller, with the link so the command is still useful.
            await interaction.followup.send(
                "機器人在這個頻道沒有「發送訊息」或「嵌入連結」權限，所以沒辦法直接發送。\n"
                f"邀請連結：{invite_url(self.bot)}\n機器人首頁：{config.HOMEPAGE_URL}",
                ephemeral=True)
            return

        try:
            await channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            log.warning("Could not post the invite in channel %s: %s", getattr(channel, "id", None), exc)
            await interaction.followup.send("❌ 發送失敗，請檢查機器人在這個頻道的權限。", ephemeral=True)
            return

        try:
            await interaction.delete_original_response()   # drop the private "thinking..." message
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InviteCog(bot))
