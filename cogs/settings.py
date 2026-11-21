"""/設定: opens the settings panel (one box with the buttons next to each setting, menus and pop-up forms).

Everything the bot can be configured with lives in the panel (see panels/): welcome, leave,
auto roles, who may use the panel, and the back office for the bot owner.
"""
import discord
from discord import app_commands
from discord.ext import commands

from panels.home import HomeView
from utils.access import require_authorized


class SettingsCog(commands.Cog, name="Settings"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="設定", description="開啟設定面板：歡迎、離開、自動身分組、權限與後臺")
    @app_commands.guild_only()
    @app_commands.check(require_authorized)
    async def settings(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        view = await HomeView.create(self.bot, interaction)
        await interaction.edit_original_response(view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SettingsCog(bot))
