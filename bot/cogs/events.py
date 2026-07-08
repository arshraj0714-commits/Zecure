"""
Events cog - global error handling and member tracking.
"""
from __future__ import annotations

import discord
from discord.ext import commands
import time

from ..core.config import config
from ..core.database import db
from ..core.logger import setup_logger
from ..utils.embeds import error as error_embed, info, warning
from ..utils.helpers import notify_owner

log = setup_logger("securitybot.events")


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, exc: Exception) -> None:
        """Handle command errors gracefully."""
        if isinstance(exc, commands.CommandNotFound):
            return  # silently ignore
        if isinstance(exc, commands.MissingPermissions):
            await ctx.send(embed=error_embed("Missing Permissions", "You don't have permission to use this command."))
            return
        if isinstance(exc, commands.NoPrivateMessage):
            await ctx.send(embed=error_embed("Server Only", "This command can only be used in a server."))
            return
        if isinstance(exc, commands.MissingRequiredArgument):
            await ctx.send(embed=error_embed("Missing Argument", f"Missing: `{exc.param.name}`\nUse `{ctx.prefix}help {ctx.command}` for usage."))
            return
        if isinstance(exc, commands.BadArgument):
            await ctx.send(embed=error_embed("Bad Argument", str(exc)))
            return
        if isinstance(exc, commands.CommandOnCooldown):
            await ctx.send(embed=warning("Cooldown", f"Try again in `{exc.retry_after:.1f}s`."), delete_after=5)
            return
        if isinstance(exc, commands.CheckFailure):
            await ctx.send(embed=error_embed("Check Failed", "You don't meet the requirements for this command."))
            return

        # Unknown error
        log.exception("Command error in %s: %s", ctx.command, exc)
        await ctx.send(embed=error_embed("Error", f"An unexpected error occurred.\n```\n{type(exc).__name__}: {exc}\n```"))

    @commands.Cog.listener()
    async def on_application_command_error(self, interaction: discord.Interaction, exc: Exception) -> None:
        log.exception("Slash command error: %s", exc)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=error_embed("Error", f"```\n{type(exc).__name__}: {exc}\n```"), ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed("Error", f"```\n{type(exc).__name__}: {exc}\n```"), ephemeral=True)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        # Register guild in DB
        existing = await db.fetchone("SELECT 1 FROM guilds WHERE guild_id = ?", (guild.id,))
        if not existing:
            await db.execute(
                "INSERT INTO guilds (guild_id, prefix, language) VALUES (?, ?, ?)",
                (guild.id, config.default_prefix, config.default_language),
            )

    @commands.Cog.listener()
    async def on_guild_available(self, guild: discord.Guild) -> None:
        log.info("Guild available: %s (%s)", guild.name, guild.id)

    @commands.Cog.listener()
    async def on_disconnect(self) -> None:
        log.warning("Bot disconnected from gateway")

    @commands.Cog.listener()
    async def on_resumed(self) -> None:
        log.info("Bot session resumed")

    @commands.Cog.listener()
    async def on_connect(self) -> None:
        log.info("Bot connected to gateway")

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context) -> None:
        log.info("Command: %s by %s (%s) in %s (%s)",
                 ctx.command, ctx.author, ctx.author.id, ctx.guild, ctx.guild.id if ctx.guild else "DM")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventsCog(bot))
