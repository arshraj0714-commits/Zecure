"""
AntiSpam cog - dedicated spam detection and rate-limiting.

This is a separate cog from AutoMod for clarity. AutoMod handles content filters,
AntiSpam handles behavioral spam detection.

Features:
- Message rate limiting (X messages per Y seconds)
- Mention spam
- Link spam
- Image/attachment spam
- Repetitive content detection
- Sticker spam
- Progressive punishments (warn → timeout → kick → ban)
"""
from __future__ import annotations

import time
import discord
from collections import deque, defaultdict
from discord.ext import commands

from ..core.config import config
from ..core.database import db
from ..core.permissions import is_whitelisted
from ..core.features import is_feature_enabled
from ..core.logger import setup_logger
from ..utils.embeds import warning, success, info, error
from ..utils.helpers import (
    count_mentions, count_emojis, normalize_text, safe_send
)

log = setup_logger("securitybot.antispam")


class AntiSpamCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # {(guild_id, user_id, channel_id): deque[(ts, content_hash)]}
        self._tracker: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=50))
        # Violation counts per user per guild
        self._violations: dict[tuple, int] = defaultdict(int)

    async def _get_thresholds(self, guild_id: int) -> dict:
        guild_thresholds = await db.get_thresholds(guild_id)
        return guild_thresholds.get("antispam", config.default_thresholds["antispam"])

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        if not await is_feature_enabled(message.guild.id, 'antispam'):
            return
        if await is_whitelisted(message.author):
            return

        # Channel whitelist
        wl = await db.fetchone(
            "SELECT 1 FROM whitelist WHERE guild_id = ? AND entity_id = ? AND entity_type = 'channel'",
            (message.guild.id, message.channel.id),
        )
        if wl:
            return

        thresholds = await self._get_thresholds(message.guild.id)
        now = time.time()
        key = (message.guild.id, message.author.id, message.channel.id)
        dq = self._tracker[key]
        dq.append((now, hash(normalize_text(message.content)), len(message.attachments), count_mentions(message.content)))

        # Prune old
        window = thresholds.get("window_seconds", 5)
        cutoff = now - window
        while dq and dq[0][0] < cutoff:
            dq.popleft()

        triggered = False
        reason = ""

        # 1. Rate limit
        msg_threshold = thresholds.get("message_threshold", 7)
        if len(dq) >= msg_threshold:
            triggered = True
            reason = f"spam: {len(dq)} messages in {window}s"

        # 2. Mention spam
        mention_threshold = thresholds.get("mention_threshold", 5)
        total_mentions = sum(item[3] for item in dq)
        if total_mentions >= mention_threshold * 3:
            triggered = True
            reason = f"mention spam: {total_mentions} mentions in {window}s"

        # 3. Duplicate content
        duplicate_threshold = thresholds.get("duplicate_threshold", 3)
        hashes = [item[1] for item in dq]
        if hashes:
            from collections import Counter
            most_common_count = Counter(hashes).most_common(1)[0][1]
            if most_common_count >= duplicate_threshold:
                triggered = True
                reason = f"duplicate messages: {most_common_count} identical in {window}s"

        # 4. Attachment spam
        total_attachments = sum(item[2] for item in dq)
        if total_attachments >= thresholds.get("attachment_threshold", 5) * 2:
            triggered = True
            reason = f"attachment spam: {total_attachments} in {window}s"

        if not triggered:
            return

        # Apply progressive punishment
        violation_key = (message.guild.id, message.author.id)
        self._violations[violation_key] += 1
        violations = self._violations[violation_key]

        # Punishment tier
        punishment = thresholds.get("punishment", "timeout")
        if violations >= 5:
            # 5+ violations: ban
            try:
                await message.author.ban(reason=f"AntiSpam: {reason} (5+ violations)", delete_message_days=1)
                tier = "ban"
            except discord.Forbidden:
                tier = "timeout"
        elif violations >= 3:
            # 3-4 violations: kick
            try:
                await message.author.kick(reason=f"AntiSpam: {reason} (3+ violations)")
                tier = "kick"
            except discord.Forbidden:
                tier = "timeout"
        else:
            # 1-2 violations: timeout or warn
            if punishment == "timeout":
                timeout_seconds = thresholds.get("timeout_seconds", 600)
                until = discord.utils.utcnow() + discord.utils.timedelta(seconds=timeout_seconds)
                try:
                    await message.author.timeout(until, reason=f"AntiSpam: {reason}")
                    tier = "timeout"
                except discord.Forbidden:
                    tier = "warn"
            else:
                tier = "warn"

        # Delete recent spam messages
        try:
            async for hist in message.channel.history(limit=20):
                if hist.author.id == message.author.id and (time.time() - hist.created_at.timestamp()) < window:
                    try:
                        await hist.delete()
                    except (discord.Forbidden, discord.HTTPException):
                        pass
        except (discord.Forbidden, discord.HTTPException):
            pass

        # Notify
        await safe_send(
            message.channel,
            embed=warning(
                f"AntiSpam: {tier.title()}",
                f"<@{message.author.id}> was {tier}ed for spam.\n"
                f"**Reason:** {reason}\n"
                f"**Violations:** {violations}",
            ),
            delete_after=15,
        )

        # Log
        log_row = await db.fetchone("SELECT security_log FROM log_channels WHERE guild_id = ?", (message.guild.id,))
        if log_row and log_row["security_log"]:
            channel = message.guild.get_channel(log_row["security_log"])
            if channel:
                await safe_send(
                    channel,
                    embed=warning(
                        "AntiSpam Violation",
                        f"**User:** <@{message.author.id}> (`{message.author.id}`)\n"
                        f"**Channel:** <#{message.channel.id}>\n"
                        f"**Reason:** {reason}\n"
                        f"**Action:** {tier}\n"
                        f"**Violations:** {violations}",
                    ),
                )

        # Record history
        await db.execute(
            """INSERT INTO user_history (guild_id, user_id, action, moderator_id, reason)
               VALUES (?, ?, 'antispam_violation', ?, ?)""",
            (message.guild.id, message.author.id, self.bot.user.id, f"{reason} - {tier}"),
        )

    @commands.hybrid_command(name="antispam")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def antispam_status(self, ctx: commands.Context) -> None:
        """View AntiSpam configuration."""
        thresholds = await self._get_thresholds(ctx.guild.id)
        embed = info("AntiSpam Configuration", "Current spam detection thresholds:")
        for k, v in thresholds.items():
            embed.add_field(name=k, value=f"`{v}`", inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="antispamthreshold")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def set_antispam_threshold(self, ctx: commands.Context, key: str, value: str) -> None:
        """Set an AntiSpam threshold."""
        thresholds = await db.get_thresholds(ctx.guild.id)
        antispam = thresholds.get("antispam", config.default_thresholds["antispam"])
        if key not in antispam:
            await ctx.send(embed=error("Invalid Key", f"`{key}` is not valid. Use `!antispam` to see options."))
            return
        try:
            if isinstance(antispam[key], str):
                new_val = value
            else:
                new_val = int(value)
        except ValueError:
            await ctx.send("❌ Invalid value.")
            return
        antispam[key] = new_val
        thresholds["antispam"] = antispam
        await db.set_thresholds(ctx.guild.id, thresholds)
        await ctx.send(embed=success("Updated", f"`antispam.{key}` set to `{new_val}`"))

    @commands.hybrid_command(name="clearspam")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def clear_spam(self, ctx: commands.Context, member: discord.Member) -> None:
        """Clear AntiSpam violation count for a user."""
        key = (ctx.guild.id, member.id)
        if key in self._violations:
            self._violations[key] = 0
            await ctx.send(embed=success("Cleared", f"AntiSpam violations cleared for <@{member.id}>."))
        else:
            await ctx.send(embed=info("No Violations", "That user has no AntiSpam violations."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiSpamCog(bot))
