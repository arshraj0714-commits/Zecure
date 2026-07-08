"""
Logging cog - centralized audit log capture & server event logging.

Captures Discord audit log events and stores them in the database.
Also sends formatted embeds to configured log channels.
"""
from __future__ import annotations

import discord
from discord.ext import commands
from discord import AuditLogAction
import time
from typing import Optional

from ..core.database import db
from ..core.features import is_feature_enabled
from ..core.logger import setup_logger
from ..utils.embeds import info, warning
from ..utils.helpers import safe_send
from ..utils.constants import Colors, Icons

log = setup_logger("securitybot.logging")


class LoggingCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _get_log_channels(self, guild_id: int) -> dict:
        row = await db.fetchone("SELECT * FROM log_channels WHERE guild_id = ?", (guild_id,))
        return dict(row) if row else {}

    async def _record_audit(self, guild_id: int, actor_id: int, action: str, target_id: int = None, target_type: str = None, reason: str = None, metadata: str = None) -> None:
        await db.execute(
            """INSERT INTO audit_log (guild_id, actor_id, action, target_id, target_type, reason, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, actor_id, action, target_id, target_type, reason, metadata),
        )

    async def _send_to_log(self, guild: discord.Guild, log_type: str, embed: discord.Embed) -> None:
        channels = await self._get_log_channels(guild.id)
        channel_id = channels.get(log_type)
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if channel:
            await safe_send(channel, embed=embed)

    # ---------- Member events ----------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not await is_feature_enabled(member.guild.id, 'logging'):
            return
        embed = discord.Embed(
            title=f"{Icons.PLUS}  Member Joined",
            color=Colors.SUCCESS,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="User", value=f"<@{member.id}> (`{member.id}`)", inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member count: {member.guild.member_count}")
        await self._send_to_log(member.guild, "member_log", embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if not await is_feature_enabled(member.guild.id, 'logging'):
            return
        embed = discord.Embed(
            title=f"{Icons.MINUS}  Member Left",
            color=Colors.WARNING,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="User", value=f"<@{member.id}> (`{member.id}`)", inline=True)
        if member.joined_at:
            embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)
        roles = [r.mention for r in member.roles if not r.is_default()]
        if roles:
            embed.add_field(name="Roles", value=" ".join(roles)[:1024], inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member count: {member.guild.member_count}")
        await self._send_to_log(member.guild, "member_log", embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if not await is_feature_enabled(after.guild.id, 'logging'):
            return
        # Role changes
        before_roles = set(before.roles)
        after_roles = set(after.roles)
        added = after_roles - before_roles
        removed = before_roles - after_roles
        if added or removed:
            embed = discord.Embed(
                title=f"{Icons.USER}  Member Roles Updated",
                color=Colors.INFO,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="User", value=f"<@{after.id}> (`{after.id}`)", inline=False)
            if added:
                embed.add_field(name="Added", value=" ".join(r.mention for r in added), inline=False)
            if removed:
                embed.add_field(name="Removed", value=" ".join(r.mention for r in removed), inline=False)
            await self._send_to_log(after.guild, "server_log", embed)

    # ---------- Channel events ----------

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        if not await is_feature_enabled(channel.guild.id, 'logging'):
            return
        embed = discord.Embed(
            title=f"{Icons.PLUS}  Channel Created",
            color=Colors.SUCCESS,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Channel", value=f"<#{channel.id}> (`{channel.id}`)", inline=True)
        embed.add_field(name="Type", value=str(channel.type), inline=True)
        embed.add_field(name="Category", value=channel.category.name if channel.category else "None", inline=True)
        await self._send_to_log(channel.guild, "server_log", embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if not await is_feature_enabled(channel.guild.id, 'logging'):
            return
        embed = discord.Embed(
            title=f"{Icons.MINUS}  Channel Deleted",
            color=Colors.DANGER,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Name", value=channel.name, inline=True)
        embed.add_field(name="Type", value=str(channel.type), inline=True)
        embed.add_field(name="ID", value=f"`{channel.id}`", inline=True)
        await self._send_to_log(channel.guild, "server_log", embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after) -> None:
        if not await is_feature_enabled(after.guild.id, 'logging'):
            return
        changes = []
        if hasattr(before, "name") and before.name != after.name:
            changes.append(f"Name: `{before.name}` → `{after.name}`")
        if hasattr(before, "topic") and before.topic != after.topic:
            changes.append(f"Topic updated")
        if hasattr(before, "slowmode_delay") and before.slowmode_delay != after.slowmode_delay:
            changes.append(f"Slowmode: `{before.slowmode_delay}s` → `{after.slowmode_delay}s`")
        if not changes:
            return
        embed = discord.Embed(
            title=f"{Icons.SETTINGS}  Channel Updated",
            color=Colors.INFO,
            timestamp=discord.utils.utcnow(),
            description="\n".join(changes),
        )
        embed.add_field(name="Channel", value=f"<#{after.id}>", inline=True)
        await self._send_to_log(after.guild, "server_log", embed)

    # ---------- Role events ----------

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        if not await is_feature_enabled(role.guild.id, 'logging'):
            return
        embed = discord.Embed(
            title=f"{Icons.PLUS}  Role Created",
            color=Colors.SUCCESS,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Role", value=f"<@&{role.id}> (`{role.id}`)", inline=True)
        embed.add_field(name="Position", value=str(role.position), inline=True)
        await self._send_to_log(role.guild, "server_log", embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        if not await is_feature_enabled(role.guild.id, 'logging'):
            return
        embed = discord.Embed(
            title=f"{Icons.MINUS}  Role Deleted",
            color=Colors.DANGER,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Name", value=role.name, inline=True)
        embed.add_field(name="ID", value=f"`{role.id}`", inline=True)
        await self._send_to_log(role.guild, "server_log", embed)

    # ---------- Message events ----------

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        if not await is_feature_enabled(message.guild.id, 'logging'):
            return
        embed = discord.Embed(
            title=f"{Icons.TRASH}  Message Deleted",
            color=Colors.WARNING,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Author", value=f"<@{message.author.id}>", inline=True)
        embed.add_field(name="Channel", value=f"<#{message.channel.id}>", inline=True)
        content = message.content or "(no text - attachments only)"
        embed.add_field(name="Content", value=content[:1024] if content else "None", inline=False)
        await self._send_to_log(message.guild, "mod_log", embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if not before.guild or before.author.bot:
            return
        if not await is_feature_enabled(before.guild.id, 'logging'):
            return
        if before.content == after.content:
            return
        embed = discord.Embed(
            title=f"{Icons.SETTINGS}  Message Edited",
            color=Colors.INFO,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Author", value=f"<@{before.author.id}>", inline=True)
        embed.add_field(name="Channel", value=f"<#{before.channel.id}>", inline=True)
        embed.add_field(name="Before", value=before.content[:1024] if before.content else "None", inline=False)
        embed.add_field(name="After", value=after.content[:1024] if after.content else "None", inline=False)
        embed.add_field(name="Jump", value=f"[Click]({after.jump_url})", inline=False)
        await self._send_to_log(before.guild, "mod_log", embed)

    # ---------- Voice events ----------

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if not await is_feature_enabled(member.guild.id, 'logging'):
            return
        if before.channel == after.channel:
            return
        embed = discord.Embed(
            title=f"{Icons.USER}  Voice State Update",
            color=Colors.INFO,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="User", value=f"<@{member.id}>", inline=True)
        if before.channel:
            embed.add_field(name="Left", value=f"<#{before.channel.id}>", inline=True)
        if after.channel:
            embed.add_field(name="Joined", value=f"<#{after.channel.id}>", inline=True)
        if not before.channel and not after.channel:
            return
        await self._send_to_log(member.guild, "voice_log", embed)

    # ---------- Commands ----------

    @commands.hybrid_command(name="setlog")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def set_log_channel(self, ctx: commands.Context, log_type: str, channel: Optional[discord.TextChannel] = None) -> None:
        """Set a log channel. Types: mod_log, server_log, member_log, voice_log, security_log, audit_log"""
        valid_types = {"mod_log", "server_log", "member_log", "voice_log", "security_log", "audit_log"}
        if log_type not in valid_types:
            await ctx.send(embed=warning("Invalid Type", f"Valid types: {', '.join(valid_types)}"))
            return
        channel_id = channel.id if channel else None
        existing = await db.fetchone("SELECT 1 FROM log_channels WHERE guild_id = ?", (ctx.guild.id,))
        if existing:
            await db.execute(
                f"UPDATE log_channels SET {log_type} = ? WHERE guild_id = ?",
                (channel_id, ctx.guild.id),
            )
        else:
            cols = ["guild_id"] + list(valid_types)
            placeholders = ", ".join(["?"] * len(cols))
            values = [ctx.guild.id] + [channel_id if t == log_type else None for t in valid_types]
            await db.execute(
                f"INSERT INTO log_channels ({', '.join(cols)}) VALUES ({placeholders})",
                tuple(values),
            )
        if channel:
            from ..utils.embeds import success
            await ctx.send(embed=success("Log Channel Set", f"`{log_type}` → <#{channel.id}>"))
        else:
            from ..utils.embeds import success
            await ctx.send(embed=success("Log Channel Cleared", f"`{log_type}` disabled."))

    @commands.hybrid_command(name="logchannels")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def list_log_channels(self, ctx: commands.Context) -> None:
        channels = await self._get_log_channels(ctx.guild.id)
        if not channels:
            await ctx.send(embed=info("No Log Channels", "Use `!setlog <type> #channel` to configure."))
            return
        embed = info("Log Channels", "Current configuration:")
        for k, v in channels.items():
            if k == "guild_id":
                continue
            value = f"<#{v}>" if v else "`disabled`"
            embed.add_field(name=k, value=value, inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="auditlog")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def audit_log(self, ctx: commands.Context, limit: int = 10) -> None:
        """View recent audit log entries (from our DB)."""
        rows = await db.fetchall(
            "SELECT * FROM audit_log WHERE guild_id = ? ORDER BY timestamp DESC LIMIT ?",
            (ctx.guild.id, min(limit, 25)),
        )
        if not rows:
            await ctx.send(embed=info("No Entries", "Audit log is empty."))
            return
        embed = info("Audit Log", f"Last {len(rows)} entries:")
        for r in rows[:15]:
            target_str = f"<@{r['target_id']}>" if r["target_type"] == "user" else f"`{r['target_id']}`"
            embed.add_field(
                name=f"{r['action'].title()} • <t:{r['timestamp']}:R>",
                value=f"**By:** <@{r['actor_id']}>\n**Target:** {target_str}\n**Reason:** {r['reason'] or 'None'}",
                inline=False,
            )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LoggingCog(bot))
