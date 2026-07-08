"""
Main bot class. Wires up intents, cogs, database, and event dispatch.
"""
from __future__ import annotations

import asyncio
import discord
from discord.ext import commands, tasks
from pathlib import Path

from ..core.config import config
from ..core.database import db
from ..core.logger import setup_logger
from ..core.i18n import load_locales
from ..utils.embeds import owner_alert, info
from ..utils.helpers import notify_owner

log = setup_logger("securitybot.bot")

INITIAL_COGS = [
    "bot.cogs.antinuke",
    "bot.cogs.automod",
    "bot.cogs.antiraid",
    "bot.cogs.antispam",
    "bot.cogs.antitamper",
    "bot.cogs.moderation",
    "bot.cogs.verification",
    "bot.cogs.backup",
    "bot.cogs.logging_cog",
    "bot.cogs.analytics",
    "bot.cogs.scheduled_tasks",
    "bot.cogs.settings",
    "bot.cogs.events",
]


class SecurityBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.all()
        super().__init__(
            command_prefix=self.get_prefix,
            intents=intents,
            help_command=None,
            case_insensitive=True,
            strip_after_prefix=True,
            owner_ids={config.owner_id},
            allowed_mentions=discord.AllowedMentions(everyone=False, replied_user=False, roles=False),
            max_messages=10_000,
        )
        self.start_time = None
        self._loaded_cogs: list[str] = []

    async def get_prefix(self, message: discord.Message) -> list[str]:
        """Per-guild prefix (default '.') + mention. Matches Cypher behavior."""
        if message.guild is None:
            # DMs: mention or default prefix
            return commands.when_mentioned_or(config.default_prefix)(self, message)
        # Per-guild prefix
        row = await db.fetchone("SELECT prefix FROM guilds WHERE guild_id = ?", (message.guild.id,))
        prefix = row["prefix"] if row else config.default_prefix
        return commands.when_mentioned_or(prefix)(self, message)

    async def setup_hook(self) -> None:
        """Async setup before connecting to gateway."""
        log.info("Running setup_hook...")
        Path("./data").mkdir(parents=True, exist_ok=True)
        Path("./logs").mkdir(parents=True, exist_ok=True)
        Path("./backups").mkdir(parents=True, exist_ok=True)

        # Database
        await db.connect()

        # Locales
        load_locales()
        log.info("Loaded %d locales", len([f for f in (Path(__file__).parent.parent / "locales").glob("*.json")]))

        # Cogs
        for cog_path in INITIAL_COGS:
            try:
                await self.load_extension(cog_path)
                self._loaded_cogs.append(cog_path)
                log.info("Loaded cog: %s", cog_path)
            except Exception as e:
                log.exception("Failed to load %s: %s", cog_path, e)

        # Sync slash commands
        try:
            synced = await self.tree.sync()
            log.info("Synced %d slash commands", len(synced))
        except Exception as e:
            log.error("Failed to sync slash commands: %s", e)

    async def on_ready(self) -> None:
        self.start_time = discord.utils.utcnow()
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        log.info("Connected to %d guilds, %d users", len(self.guilds), sum(g.member_count or 0 for g in self.guilds))
        log.info("Owner ID: %s", config.owner_id)

        # Set presence (Zecurity brand)
        try:
            await self.change_presence(
                status=discord.Status.do_not_disturb,
                activity=discord.Activity(
                    type=discord.ActivityType.playing,
                    name=f".help | Zecurity • Built By Arsh",
                ),
            )
        except Exception:
            pass

        # Notify owner that bot is online
        await notify_owner(
            self,
            info(
                "SecurityBot Online",
                f"✅ Bot is now online.\n"
                f"**Servers:** {len(self.guilds)}\n"
                f"**Users:** {sum(g.member_count or 0 for g in self.guilds)}\n"
                f"**Cogs loaded:** {len(self._loaded_cogs)}",
            ),
        )

    async def close(self) -> None:
        log.info("Shutting down...")
        await db.close()
        await super.close()


async def main() -> None:
    errors = config.validate()
    if errors:
        for e in errors:
            log.error("Config error: %s", e)
        raise SystemExit(1)

    bot = SecurityBot()
    try:
        await bot.start(config.token)
    except KeyboardInterrupt:
        await bot.close()
    except Exception as e:
        log.exception("Fatal error: %s", e)
        raise
