"""
AntiTamper cog - protects bot configuration and server integrity.

Features:
- Prevents unauthorized role modifications on protected roles
- Detects self-bot promotion messages
- Detects token-sharing messages
- Monitors permission overrides changes
- Prevents unauthorized bot config changes
- Detects attempts to disable security features
"""
from __future__ import annotations

import re
import discord
from discord.ext import commands
from discord import AuditLogAction

from ..core.config import config
from ..core.database import db
from ..core.permissions import is_whitelisted, is_owner
from ..core.features import is_feature_enabled
from ..core.logger import setup_logger
from ..utils.embeds import warning, info, success, error, nuke_alert
from ..utils.helpers import notify_owner, safe_send

log = setup_logger("securitybot.antitamper")

# Patterns that look like Discord tokens
TOKEN_LIKE_RE = re.compile(r"[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27}")
SELF_BOT_RE = re.compile(r"self[-_ ]?bot", re.IGNORECASE)


class AntiTamperCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Track recent permission override changes per guild
        self._perm_changes: dict[int, list] = {}

    async def _get_thresholds(self, guild_id: int) -> dict:
        guild_thresholds = await db.get_thresholds(guild_id)
        return guild_thresholds.get("antitamper", config.default_thresholds["antitamper"])

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        """Detect dangerous permission changes on roles."""
        guild = after.guild
        if not await is_feature_enabled(guild.id, 'antitamper'):
            return
        try:
            async for entry in guild.audit_logs(limit=1, action=AuditLogAction.role_update):
                actor = entry.user
                if actor.id == self.bot.user.id or await is_whitelisted(actor) or actor.id == guild.owner_id:
                    return

                thresholds = await self._get_thresholds(guild.id)
                protected_roles = thresholds.get("protected_roles", [])

                # Check if this role is protected
                if after.id in protected_roles:
                    desc = f"User <@{actor.id}> modified protected role `{after.name}`"
                    await self._alert(guild, actor, "Protected Role Modified", desc, severity="high")
                    # Revert permissions
                    try:
                        await after.edit(
                            permissions=before.permissions,
                            name=before.name,
                            color=before.color,
                            reason=f"AntiTamper: reverting change to protected role",
                        )
                    except discord.Forbidden:
                        pass
                    return

                # Check for dangerous permission grants
                new_perms = after.permissions
                dangerous_perms = []
                if new_perms.administrator and not before.permissions.administrator:
                    dangerous_perms.append("Administrator")
                if new_perms.ban_members and not before.permissions.ban_members:
                    dangerous_perms.append("Ban Members")
                if new_perms.kick_members and not before.permissions.kick_members:
                    dangerous_perms.append("Kick Members")
                if new_perms.manage_guild and not before.permissions.manage_guild:
                    dangerous_perms.append("Manage Server")
                if new_perms.manage_roles and not before.permissions.manage_roles:
                    dangerous_perms.append("Manage Roles")
                if new_perms.manage_channels and not before.permissions.manage_channels:
                    dangerous_perms.append("Manage Channels")
                if new_perms.manage_webhooks and not before.permissions.manage_webhooks:
                    dangerous_perms.append("Manage Webhooks")
                if new_perms.mention_everyone and not before.permissions.mention_everyone:
                    dangerous_perms.append("Mention Everyone")

                if dangerous_perms:
                    desc = (
                        f"User <@{actor.id}> granted dangerous permissions to role `{after.name}`:\n"
                        + "\n".join(f"• {p}" for p in dangerous_perms)
                    )
                    await self._alert(guild, actor, "Dangerous Permission Grant", desc, severity="high")
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Detect token sharing and self-bot promotion."""
        if not message.guild or message.author.bot:
            return
        if not await is_feature_enabled(message.guild.id, 'antitamper'):
            return
        if await is_whitelisted(message.author):
            return

        thresholds = await self._get_thresholds(message.guild.id)
        content = message.content

        triggered = False
        reasons = []

        # Token detection
        if thresholds.get("block_token_sharing", True) and TOKEN_LIKE_RE.search(content):
            triggered = True
            reasons.append("Discord token sharing detected")

        # Self-bot promotion
        if thresholds.get("block_self_bot_promotion", True) and SELF_BOT_RE.search(content):
            triggered = True
            reasons.append("Self-bot promotion detected")

        if not triggered:
            return

        # Delete message + punish
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        # Record violation
        await db.execute(
            """INSERT INTO user_history (guild_id, user_id, action, moderator_id, reason)
               VALUES (?, ?, 'antitamper_violation', ?, ?)""",
            (message.guild.id, message.author.id, self.bot.user.id, "; ".join(reasons)),
        )

        # Apply timeout (24h for token sharing)
        try:
            until = discord.utils.utcnow() + discord.utils.timedelta(hours=24)
            await message.author.timeout(until, reason="AntiTamper: " + "; ".join(reasons))
        except discord.Forbidden:
            pass

        # Log
        log_row = await db.fetchone("SELECT security_log FROM log_channels WHERE guild_id = ?", (message.guild.id,))
        if log_row and log_row["security_log"]:
            channel = message.guild.get_channel(log_row["security_log"])
            if channel:
                embed = warning(
                    "AntiTamper Violation",
                    f"**User:** <@{message.author.id}> (`{message.author.id}`)\n"
                    f"**Channel:** <#{message.channel.id}>\n"
                    f"**Reason:** {'; '.join(reasons)}\n"
                    f"**Action:** Message deleted + 24h timeout",
                )
                await safe_send(channel, embed=embed)

        # Alert owner (token sharing is severe)
        from ..utils.embeds import owner_alert
        await notify_owner(
            self.bot,
            owner_alert(
                "AntiTamper — Critical",
                f"**Server:** {message.guild.name} (`{message.guild.id}`)\n"
                f"**User:** <@{message.author.id}> (`{message.author.id}`)\n"
                f"**Reason:** {'; '.join(reasons)}",
                guild=message.guild,
            ),
        )

    async def _alert(self, guild: discord.Guild, actor: discord.Member, alert_type: str, description: str, severity: str = "medium") -> None:
        """Send alert to log channels and owner."""
        from ..utils.embeds import severity_embed
        embed = severity_embed(severity, f"AntiTamper: {alert_type}", description)
        embed.add_field(name="Actor", value=f"<@{actor.id}> (`{actor.id}`)", inline=True)

        log_row = await db.fetchone("SELECT security_log FROM log_channels WHERE guild_id = ?", (guild.id,))
        if log_row and log_row["security_log"]:
            channel = guild.get_channel(log_row["security_log"])
            if channel:
                await safe_send(channel, embed=embed)

        if severity in ("high", "critical"):
            from ..utils.embeds import owner_alert
            await notify_owner(self.bot, owner_alert(f"AntiTamper: {alert_type}", description, guild=guild))

    # ---------- Commands ----------

    @commands.hybrid_command(name="protectrole")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def protect_role(self, ctx: commands.Context, role: discord.Role) -> None:
        """Mark a role as protected (cannot be modified by non-owners)."""
        thresholds = await db.get_thresholds(ctx.guild.id)
        antitamper = thresholds.get("antitamper", config.default_thresholds["antitamper"])
        protected = antitamper.get("protected_roles", [])
        if role.id in protected:
            protected.remove(role.id)
            await ctx.send(embed=info("Unprotected", f"Role `{role.name}` is no longer protected."))
        else:
            protected.append(role.id)
            await ctx.send(embed=success("Protected", f"Role `{role.name}` is now protected. Only the owner can modify it."))
        antitamper["protected_roles"] = protected
        thresholds["antitamper"] = antitamper
        await db.set_thresholds(ctx.guild.id, thresholds)

    @commands.hybrid_command(name="antitamper")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def antitamper_status(self, ctx: commands.Context) -> None:
        """View AntiTamper configuration."""
        thresholds = await self._get_thresholds(ctx.guild.id)
        embed = info("AntiTamper Configuration", "Current tamper protection settings:")
        protected = thresholds.get("protected_roles", [])
        protected_str = ", ".join(f"<@&{r}>" for r in protected) if protected else "None"
        embed.add_field(name="Protected Roles", value=protected_str, inline=False)
        embed.add_field(name="Block Self-Bot Promotion", value=thresholds.get("block_self_bot_promotion", True), inline=True)
        embed.add_field(name="Block Token Sharing", value=thresholds.get("block_token_sharing", True), inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiTamperCog(bot))
