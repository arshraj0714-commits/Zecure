"""
Moderation cog - standard mod commands with rich logging.

Commands:
- !ban / !unban
- !kick
- !mute / !unmute (timeout based)
- !warn
- !purge / !clear
- !lock / !unlock
- !slowmode
- !userinfo / !serverinfo
- !history
- !warnings
- !clearwarnings
- !modlogs
"""
from __future__ import annotations

import discord
from discord.ext import commands
from typing import Optional

from ..core.config import config
from ..core.database import db
from ..core.permissions import is_admin
from ..core.logger import setup_logger
from ..utils.embeds import (
    success, error, warning, info, user_embed, punishment_embed, stats_embed,
)
from ..utils.helpers import parse_duration, format_duration, can_act_on, safe_send
from ..utils.constants import Colors, Icons

log = setup_logger("securitybot.moderation")


class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ---------- Helpers ----------
    async def _log_moderation(self, guild_id: int, user_id: int, mod_id: int, action: str, reason: str, duration: Optional[int] = None) -> None:
        await db.execute(
            """INSERT INTO user_history (guild_id, user_id, action, moderator_id, reason, duration)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (guild_id, user_id, action, mod_id, reason, duration),
        )
        # Also write to audit log
        import json
        await db.execute(
            """INSERT INTO audit_log (guild_id, actor_id, action, target_id, target_type, reason)
               VALUES (?, ?, ?, ?, 'user', ?)""",
            (guild_id, mod_id, action, user_id, reason),
        )

        # Send to mod-log channel if configured
        row = await db.fetchone("SELECT mod_log FROM log_channels WHERE guild_id = ?", (guild_id,))
        if row and row["mod_log"]:
            from ..core.config import config as cfg
            guild = self.bot.get_guild(guild_id)
            if guild:
                channel = guild.get_channel(row["mod_log"])
                if channel:
                    embed = discord.Embed(
                        title=f"{Icons.BAN}  Moderation Action: {action.title()}",
                        color=Colors.DANGER,
                        timestamp=discord.utils.utcnow(),
                    )
                    embed.add_field(name="User", value=f"<@{user_id}> (`{user_id}`)", inline=True)
                    embed.add_field(name="Moderator", value=f"<@{mod_id}> (`{mod_id}`)", inline=True)
                    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
                    if duration:
                        embed.add_field(name="Duration", value=format_duration(duration), inline=True)
                    embed.set_footer(text="SecurityBot Enterprise")
                    await safe_send(channel, embed=embed)

    # ---------- Ban ----------
    @commands.hybrid_command(name="ban")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided") -> None:
        if not can_act_on(self.bot, ctx.guild, ctx.author, member):
            await ctx.send(embed=error("Cannot Ban", "You don't have permission to ban this user."))
            return
        try:
            await member.ban(reason=f"{ctx.author} ({ctx.author.id}): {reason}", delete_message_days=1)
            await self._log_moderation(ctx.guild.id, member.id, ctx.author.id, "ban", reason)
            await ctx.send(embed=punishment_embed("Ban", member, ctx.author, reason))
            try:
                await member.send(embed=warning("Banned", f"You were banned from **{ctx.guild.name}**.\n**Reason:** {reason}"))
            except discord.Forbidden:
                pass
        except discord.Forbidden:
            await ctx.send(embed=error("Missing Permissions", "I don't have permission to ban this user."))

    @commands.hybrid_command(name="unban")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user_id: int, *, reason: str = "No reason provided") -> None:
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=f"{ctx.author}: {reason}")
            await self._log_moderation(ctx.guild.id, user_id, ctx.author.id, "unban", reason)
            await ctx.send(embed=success("Unbanned", f"<@{user_id}> has been unbanned."))
        except discord.NotFound:
            await ctx.send(embed=error("Not Found", "That user is not banned."))
        except discord.Forbidden:
            await ctx.send(embed=error("Missing Permissions", "I don't have permission to unban users."))

    # ---------- Kick ----------
    @commands.hybrid_command(name="kick")
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided") -> None:
        if not can_act_on(self.bot, ctx.guild, ctx.author, member):
            await ctx.send(embed=error("Cannot Kick", "You don't have permission to kick this user."))
            return
        try:
            await member.kick(reason=f"{ctx.author} ({ctx.author.id}): {reason}")
            await self._log_moderation(ctx.guild.id, member.id, ctx.author.id, "kick", reason)
            await ctx.send(embed=punishment_embed("Kick", member, ctx.author, reason))
        except discord.Forbidden:
            await ctx.send(embed=error("Missing Permissions", "I don't have permission to kick this user."))

    # ---------- Mute / Timeout ----------
    @commands.hybrid_command(name="mute", aliases=["timeout"])
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, duration: str = "10m", *, reason: str = "No reason provided") -> None:
        if not can_act_on(self.bot, ctx.guild, ctx.author, member):
            await ctx.send(embed=error("Cannot Mute", "You don't have permission to mute this user."))
            return
        seconds = parse_duration(duration) or 600
        if seconds > 2419200:  # 28 days
            await ctx.send(embed=error("Duration Too Long", "Max timeout is 28 days."))
            return
        try:
            until = discord.utils.utcnow() + discord.utils.timedelta(seconds=seconds)
            await member.timeout(until, reason=f"{ctx.author}: {reason}")
            await self._log_moderation(ctx.guild.id, member.id, ctx.author.id, "mute", reason, seconds)
            await ctx.send(embed=punishment_embed("Timeout", member, ctx.author, reason, format_duration(seconds)))
        except discord.Forbidden:
            await ctx.send(embed=error("Missing Permissions", "I don't have permission to timeout this user."))

    @commands.hybrid_command(name="unmute", aliases=["untimeout"])
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided") -> None:
        try:
            await member.timeout(None, reason=f"{ctx.author}: {reason}")
            await self._log_moderation(ctx.guild.id, member.id, ctx.author.id, "unmute", reason)
            await ctx.send(embed=success("Unmuted", f"<@{member.id}> has been unmuted."))
        except discord.Forbidden:
            await ctx.send(embed=error("Missing Permissions", "I don't have permission to unmute this user."))

    # ---------- Warn ----------
    @commands.hybrid_command(name="warn")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str) -> None:
        await self._log_moderation(ctx.guild.id, member.id, ctx.author.id, "warn", reason)
        await ctx.send(embed=punishment_embed("Warning", member, ctx.author, reason))
        try:
            await member.send(embed=warning("You Were Warned", f"In **{ctx.guild.name}**\n**Reason:** {reason}\n**Moderator:** {ctx.author.mention}"))
        except discord.Forbidden:
            pass

    @commands.hybrid_command(name="warnings")
    @commands.guild_only()
    async def warnings(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        member = member or ctx.author
        rows = await db.fetchall(
            "SELECT * FROM user_history WHERE guild_id = ? AND user_id = ? AND action = 'warn' ORDER BY timestamp DESC LIMIT 20",
            (ctx.guild.id, member.id),
        )
        if not rows:
            await ctx.send(embed=info("No Warnings", f"<@{member.id}> has no warnings."))
            return
        embed = user_embed(member, f"Warnings for {member.display_name}", color=Colors.WARNING)
        for r in rows[:10]:
            mod = self.bot.get_user(r["moderator_id"])
            mod_str = f"<@{r['moderator_id']}>" if mod else f"`{r['moderator_id']}`"
            embed.add_field(
                name=f"<t:{r['timestamp']}:R>",
                value=f"**By:** {mod_str}\n**Reason:** {r['reason']}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="clearwarnings")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def clear_warnings(self, ctx: commands.Context, member: discord.Member) -> None:
        await db.execute(
            "DELETE FROM user_history WHERE guild_id = ? AND user_id = ? AND action = 'warn'",
            (ctx.guild.id, member.id),
        )
        await ctx.send(embed=success("Cleared", f"All warnings for <@{member.id}> have been cleared."))

    # ---------- Purge ----------
    @commands.hybrid_command(name="purge", aliases=["clear"])
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def purge(self, ctx: commands.Context, count: int) -> None:
        if count < 1 or count > 1000:
            await ctx.send(embed=error("Invalid Count", "Must be between 1 and 1000."))
            return
        try:
            deleted = await ctx.channel.purge(limit=count + 1)
            await self._log_moderation(ctx.guild.id, 0, ctx.author.id, "purge", f"Cleared {len(deleted)-1} messages in #{ctx.channel.name}")
            await ctx.send(embed=success("Purged", f"Deleted `{len(deleted)-1}` messages."), delete_after=5)
        except discord.Forbidden:
            await ctx.send(embed=error("Missing Permissions", "I don't have permission to manage messages."))

    # ---------- Lock / Unlock ----------
    @commands.hybrid_command(name="lock")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        channel = channel or ctx.channel
        try:
            await channel.set_permissions(ctx.guild.default_role, send_messages=False, reason=f"Lock by {ctx.author}")
            await self._log_moderation(ctx.guild.id, 0, ctx.author.id, "lock", f"Locked #{channel.name}")
            await ctx.send(embed=warning("Channel Locked", f"<#{channel.id}> is now read-only."))
        except discord.Forbidden:
            await ctx.send(embed=error("Missing Permissions", "I don't have permission to manage this channel."))

    @commands.hybrid_command(name="unlock")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        channel = channel or ctx.channel
        try:
            await channel.set_permissions(ctx.guild.default_role, send_messages=None, reason=f"Unlock by {ctx.author}")
            await self._log_moderation(ctx.guild.id, 0, ctx.author.id, "unlock", f"Unlocked #{channel.name}")
            await ctx.send(embed=success("Channel Unlocked", f"<#{channel.id}> is no longer read-only."))
        except discord.Forbidden:
            await ctx.send(embed=error("Missing Permissions", "I don't have permission to manage this channel."))

    # ---------- Slowmode ----------
    @commands.hybrid_command(name="slowmode")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context, seconds: int = 0) -> None:
        if seconds < 0 or seconds > 21600:
            await ctx.send(embed=error("Invalid", "Slowmode must be 0-21600 seconds."))
            return
        try:
            await ctx.channel.edit(slowmode_delay=seconds, reason=f"Slowmode set by {ctx.author}")
            await ctx.send(embed=success("Slowmode Updated", f"Slowmode is now `{seconds}s`."))
        except discord.Forbidden:
            await ctx.send(embed=error("Missing Permissions", "Cannot edit this channel."))

    # ---------- User Info ----------
    @commands.hybrid_command(name="userinfo", aliases=["whois"])
    @commands.guild_only()
    async def userinfo(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        member = member or ctx.author
        embed = user_embed(member, f"User Info — {member.display_name}", color=Colors.PRIMARY)
        if member.joined_at:
            embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Roles", value=f"{len(member.roles)}", inline=True)
        embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
        embed.add_field(name="Boosting", value="Yes" if member.premium_since else "No", inline=True)
        embed.add_field(name="Pending", value="Yes" if member.pending else "No", inline=True)
        embed.add_field(name="Bot", value="Yes" if member.bot else "No", inline=True)
        # Show recent mod actions
        history = await db.fetchall(
            "SELECT action, reason, timestamp FROM user_history WHERE guild_id = ? AND user_id = ? ORDER BY timestamp DESC LIMIT 5",
            (ctx.guild.id, member.id),
        )
        if history:
            history_str = "\n".join(f"• <t:{r['timestamp']}:R> **{r['action']}** - {r['reason']}" for r in history)
        else:
            history_str = "No recorded actions"
        embed.add_field(name="Recent History", value=history_str[:1024], inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="serverinfo", aliases=["guildinfo"])
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context) -> None:
        g = ctx.guild
        embed = info(f"Server Info — {g.name}", color=Colors.PRIMARY)
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="Owner", value=f"<@{g.owner_id}>", inline=True)
        embed.add_field(name="Created", value=f"<t:{int(g.created_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Members", value=str(g.member_count), inline=True)
        embed.add_field(name="Channels", value=str(len(g.channels)), inline=True)
        embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
        embed.add_field(name="Boosts", value=f"Lvl {g.premium_tier} ({g.premium_subscription_count})", inline=True)
        embed.add_field(name="Verification Level", value=str(g.verification_level), inline=True)
        embed.add_field(name="2FA Required", value="Yes" if g.mfa_level else "No", inline=True)
        await ctx.send(embed=embed)

    # ---------- History ----------
    @commands.hybrid_command(name="history")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def history(self, ctx: commands.Context, member: discord.Member) -> None:
        rows = await db.fetchall(
            "SELECT * FROM user_history WHERE guild_id = ? AND user_id = ? ORDER BY timestamp DESC LIMIT 30",
            (ctx.guild.id, member.id),
        )
        if not rows:
            await ctx.send(embed=info("No History", f"<@{member.id}> has no recorded actions."))
            return
        embed = user_embed(member, f"History — {member.display_name}", color=Colors.INFO)
        for r in rows[:15]:
            mod = self.bot.get_user(r["moderator_id"])
            mod_str = f"<@{r['moderator_id']}>" if mod else f"`{r['moderator_id']}`"
            duration_str = f" ({format_duration(r['duration'])})" if r["duration"] else ""
            embed.add_field(
                name=f"**{r['action'].title()}**{duration_str} • <t:{r['timestamp']}:R>",
                value=f"By: {mod_str}\nReason: {r['reason'] or 'None'}",
                inline=False,
            )
        await ctx.send(embed=embed)

    # ---------- Incident reports ----------
    @commands.hybrid_command(name="incidents")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def incidents(self, ctx: commands.Context, resolved: str = "false") -> None:
        show_resolved = resolved.lower() in ("true", "1", "yes")
        if show_resolved:
            rows = await db.fetchall(
                "SELECT * FROM incidents WHERE guild_id = ? ORDER BY timestamp DESC LIMIT 20",
                (ctx.guild.id,),
            )
        else:
            rows = await db.fetchall(
                "SELECT * FROM incidents WHERE guild_id = ? AND resolved = 0 ORDER BY timestamp DESC LIMIT 20",
                (ctx.guild.id,),
            )
        if not rows:
            await ctx.send(embed=info("No Incidents", "No incidents to display."))
            return
        embed = info("Incident Reports", f"Showing {len(rows)} incidents:")
        for r in rows[:10]:
            status = "✅ Resolved" if r["resolved"] else "⏳ Open"
            embed.add_field(
                name=f"#{r['id']} — {r['type'].title()} [{r['severity'].upper()}] • <t:{r['timestamp']}:R>",
                value=f"{r['description'][:200]}\n**Status:** {status}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="resolveincident")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def resolve_incident(self, ctx: commands.Context, incident_id: int) -> None:
        import time
        cur = await db.execute(
            "UPDATE incidents SET resolved = 1, resolved_at = ? WHERE id = ? AND guild_id = ?",
            (int(time.time()), incident_id, ctx.guild.id),
        )
        if cur.rowcount:
            await ctx.send(embed=success("Resolved", f"Incident #{incident_id} marked as resolved."))
        else:
            await ctx.send(embed=error("Not Found", "That incident does not exist in this server."))

    # ---------- Set Prefix (Cypher-style) ----------

    @commands.hybrid_command(name="setprefix")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def set_prefix(self, ctx: commands.Context, prefix: str) -> None:
        """Set a custom command prefix for this server."""
        if len(prefix) > 5:
            await ctx.send(embed=error("Too Long", "Prefix must be 5 characters or less."))
            return
        await db._ensure_guild_exists(ctx.guild.id)
        existing = await db.fetchone("SELECT 1 FROM guilds WHERE guild_id = ?", (ctx.guild.id,))
        if existing:
            await db.execute("UPDATE guilds SET prefix = ? WHERE guild_id = ?", (prefix, ctx.guild.id))
        else:
            await db.execute("INSERT INTO guilds (guild_id, prefix) VALUES (?, ?)", (ctx.guild.id, prefix))
        await ctx.send(embed=success("Prefix Updated", f"✅ New prefix: `{prefix}`"))

    # ---------- Nuke (Cypher-style channel cleanup) ----------

    @commands.hybrid_command(name="nuke")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def nuke(self, ctx: commands.Context) -> None:
        """Clone and delete the current channel (removes all messages)."""
        try:
            new_channel = await ctx.channel.clone(reason=f"Nuke by {ctx.author}")
            await new_channel.edit(position=ctx.channel.position)
            await ctx.channel.delete(reason=f"Nuke by {ctx.author}")
            await new_channel.send(embed=success("Channel Nuked", f"💥 Nuked by {ctx.author.mention}"))
        except discord.Forbidden:
            await ctx.send(embed=error("Missing Permissions", "I need Manage Channels permission."))

    # ---------- Lock All / Unlock All ----------

    @commands.hybrid_command(name="lockall")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def lockall(self, ctx: commands.Context) -> None:
        """Lock all text channels in the server."""
        locked = 0
        for channel in ctx.guild.text_channels:
            try:
                await channel.set_permissions(ctx.guild.default_role, send_messages=False, reason=f"Lockall by {ctx.author}")
                locked += 1
            except discord.Forbidden:
                pass
        await ctx.send(embed=warning("All Channels Locked", f"🔒 Locked **{locked}/{len(ctx.guild.text_channels)}** text channels."))

    @commands.hybrid_command(name="unlockall")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def unlockall(self, ctx: commands.Context) -> None:
        """Unlock all text channels in the server."""
        unlocked = 0
        for channel in ctx.guild.text_channels:
            try:
                await channel.set_permissions(ctx.guild.default_role, send_messages=None, reason=f"Unlockall by {ctx.author}")
                unlocked += 1
            except discord.Forbidden:
                pass
        await ctx.send(embed=success("All Channels Unlocked", f"🔓 Unlocked **{unlocked}/{len(ctx.guild.text_channels)}** text channels."))

    # ---------- Hide All / Unhide All ----------

    @commands.hybrid_command(name="hideall")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def hideall(self, ctx: commands.Context) -> None:
        """Hide all text channels from @everyone."""
        hidden = 0
        for channel in ctx.guild.text_channels:
            try:
                await channel.set_permissions(ctx.guild.default_role, view_channel=False, reason=f"Hideall by {ctx.author}")
                hidden += 1
            except discord.Forbidden:
                pass
        await ctx.send(embed=warning("All Channels Hidden", f"👁️ Hidden **{hidden}/{len(ctx.guild.text_channels)}** text channels."))

    @commands.hybrid_command(name="unhideall")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def unhideall(self, ctx: commands.Context) -> None:
        """Unhide all text channels from @everyone."""
        unhidden = 0
        for channel in ctx.guild.text_channels:
            try:
                await channel.set_permissions(ctx.guild.default_role, view_channel=None, reason=f"Unhideall by {ctx.author}")
                unhidden += 1
            except discord.Forbidden:
                pass
        await ctx.send(embed=success("All Channels Unhidden", f"👁️ Unhidden **{unhidden}/{len(ctx.guild.text_channels)}** text channels."))

    # ---------- Avatar ----------

    @commands.hybrid_command(name="avatar", aliases=["av", "pfp"])
    async def avatar(self, ctx: commands.Context, member: discord.Member = None) -> None:
        """View a user's avatar."""
        member = member or ctx.author
        embed = discord.Embed(
            title=f"Avatar — {member.display_name}",
            color=Colors.PRIMARY,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_image(url=member.display_avatar.url)
        embed.set_footer(text="Zecurity • Built By Arsh")
        await ctx.send(embed=embed)

    # ---------- Server Icon ----------

    @commands.hybrid_command(name="servericon", aliases=["guildicon", "sicon"])
    async def server_icon(self, ctx: commands.Context) -> None:
        """View the server's icon."""
        if not ctx.guild.icon:
            await ctx.send(embed=error("No Icon", "This server has no icon."))
            return
        embed = discord.Embed(
            title=f"Server Icon — {ctx.guild.name}",
            color=Colors.PRIMARY,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_image(url=ctx.guild.icon.url)
        embed.set_footer(text="Zecurity • Built By Arsh")
        await ctx.send(embed=embed)

    # ---------- Member Count ----------

    @commands.hybrid_command(name="membercount", aliases=["mc"])
    @commands.guild_only()
    async def member_count(self, ctx: commands.Context) -> None:
        """View the server's member count."""
        guild = ctx.guild
        total = guild.member_count or 0
        bots = sum(1 for m in guild.members if m.bot)
        humans = total - bots
        online = sum(1 for m in guild.members if m.status != discord.Status.offline) if not guild.large else "Unknown"
        embed = info(f"Member Count — {guild.name}", "")
        embed.add_field(name="Total", value=f"`{total}`", inline=True)
        embed.add_field(name="Humans", value=f"`{humans}`", inline=True)
        embed.add_field(name="Bots", value=f"`{bots}`", inline=True)
        embed.add_field(name="Online", value=f"`{online}`", inline=True)
        embed.set_footer(text="Zecurity • Built By Arsh")
        await ctx.send(embed=embed)

    # ---------- Role Info ----------

    @commands.hybrid_command(name="roleinfo", aliases=["rinfo"])
    @commands.guild_only()
    async def role_info(self, ctx: commands.Context, role: discord.Role) -> None:
        """View information about a role."""
        embed = info(f"Role Info — {role.name}", "")
        embed.add_field(name="ID", value=f"`{role.id}`", inline=True)
        embed.add_field(name="Members", value=f"`{len(role.members)}`", inline=True)
        embed.add_field(name="Position", value=f"`{role.position}`", inline=True)
        embed.add_field(name="Color", value=f"`#{role.color.value:06x}`", inline=True)
        embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
        embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)
        embed.add_field(name="Created", value=f"<t:{int(role.created_at.timestamp())}:R>", inline=True)
        perms = []
        if role.permissions.administrator:
            perms.append("Administrator")
        if role.permissions.ban_members:
            perms.append("Ban Members")
        if role.permissions.kick_members:
            perms.append("Kick Members")
        if role.permissions.manage_guild:
            perms.append("Manage Server")
        if role.permissions.manage_roles:
            perms.append("Manage Roles")
        if role.permissions.manage_channels:
            perms.append("Manage Channels")
        embed.add_field(name="Key Permissions", value=", ".join(perms) if perms else "None", inline=False)
        embed.set_footer(text="Zecurity • Built By Arsh")
        await ctx.send(embed=embed)

    # ---------- In Role ----------

    @commands.hybrid_command(name="inrole", aliases=["rolemembers"])
    @commands.guild_only()
    async def in_role(self, ctx: commands.Context, role: discord.Role) -> None:
        """List members in a role."""
        members = role.members
        if not members:
            await ctx.send(embed=info("No Members", f"No members have the {role.mention} role."))
            return
        embed = info(f"Members in {role.name} — {len(members)}", "")
        member_list = "\n".join(f"• {m.mention} (`{m.id}`)" for m in members[:30])
        embed.add_field(name="Members", value=member_list[:1024], inline=False)
        if len(members) > 30:
            embed.add_field(name="Note", value=f"Showing first 30 of {len(members)} members", inline=False)
        embed.set_footer(text="Zecurity • Built By Arsh")
        await ctx.send(embed=embed)

    # ---------- Role All ----------

    @commands.hybrid_command(name="roleall")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def role_all(self, ctx: commands.Context, role: discord.Role, target: str = "all") -> None:
        """Add a role to all members. Targets: all, humans, bots"""
        if role.position >= ctx.guild.me.top_role.position:
            await ctx.send(embed=error("Role Too High", "That role is above my top role."))
            return
        if target not in ("all", "humans", "bots"):
            await ctx.send(embed=error("Invalid Target", "Use: all, humans, or bots"))
            return

        msg = await ctx.send(embed=info("Adding Role...", f"Adding {role.mention} to **{target}** members. This may take a moment."))
        added = 0
        failed = 0
        for member in ctx.guild.members:
            if target == "humans" and member.bot:
                continue
            if target == "bots" and not member.bot:
                continue
            if role in member.roles:
                continue
            try:
                await member.add_roles(role, reason=f"roleall by {ctx.author}")
                added += 1
            except discord.Forbidden:
                failed += 1
            except discord.HTTPException:
                failed += 1
        await msg.edit(embed=success("Role Added", f"✅ Added {role.mention} to **{added}** members.\nFailed: {failed}"))

    @commands.hybrid_command(name="rolehumans")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def role_humans(self, ctx: commands.Context, role: discord.Role) -> None:
        """Add a role to all humans."""
        await self.role_all(ctx, role, "humans")

    @commands.hybrid_command(name="rolebots")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def role_bots(self, ctx: commands.Context, role: discord.Role) -> None:
        """Add a role to all bots."""
        await self.role_all(ctx, role, "bots")

    # ---------- Steal Emoji ----------

    @commands.hybrid_command(name="steal", aliases=["addemoji"])
    @commands.guild_only()
    @commands.has_permissions(manage_emojis=True)
    async def steal_emoji(self, ctx: commands.Context, emoji: str, name: str = None) -> None:
        """Add an emoji to the server. Usage: .steal <emoji> [name]"""
        import aiohttp
        try:
            if emoji.startswith("<") and emoji.endswith(">"):
                # Custom emoji
                emoji_id = int(emoji.split(":")[-1].rstrip(">"))
                animated = emoji.startswith("<a:")
                ext = "gif" if animated else "png"
                url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
                name = name or emoji.split(":")[1]
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            await ctx.send(embed=error("Error", "Could not download emoji."))
                            return
                        data = await resp.read()
                created = await ctx.guild.create_custom_emoji(name=name, image=data, reason=f"Steal by {ctx.author}")
                await ctx.send(embed=success("Emoji Added", f"✅ Added emoji `{created.name}` to the server."))
            else:
                await ctx.send(embed=error("Invalid Emoji", "Please provide a custom emoji (with <a:name:id> format)."))
        except discord.Forbidden:
            await ctx.send(embed=error("Missing Permissions", "I need Manage Emojis permission."))
        except Exception as e:
            await ctx.send(embed=error("Error", f"```\n{e}\n```"))

    # ---------- Unban All ----------

    @commands.hybrid_command(name="unbanall")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    async def unban_all(self, ctx: commands.Context) -> None:
        """Unban all banned users."""
        msg = await ctx.send(embed=info("Unbanning...", "Fetching ban list..."))
        try:
            bans = [b async for b in ctx.guild.bans(limit=None)]
        except discord.Forbidden:
            await msg.edit(embed=error("Missing Permissions", "I cannot view bans."))
            return
        unbanned = 0
        for ban_entry in bans:
            try:
                await ctx.guild.unban(ban_entry.user, reason=f"Unbanall by {ctx.author}")
                unbanned += 1
            except discord.Forbidden:
                pass
        await msg.edit(embed=success("Unbanned All", f"✅ Unbanned **{unbanned}** users."))

    # ---------- Purge Filters ----------

    @commands.hybrid_command(name="purgebots")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def purge_bots(self, ctx: commands.Context, count: int = 50) -> None:
        """Delete messages from bots."""
        await self._purge_filter(ctx, count, lambda m: m.author.bot)

    @commands.hybrid_command(name="purgehumans")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def purge_humans(self, ctx: commands.Context, count: int = 50) -> None:
        """Delete messages from humans."""
        await self._purge_filter(ctx, count, lambda m: not m.author.bot)

    @commands.hybrid_command(name="purgeuser")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def purge_user(self, ctx: commands.Context, member: discord.Member, count: int = 50) -> None:
        """Delete messages from a specific user."""
        await self._purge_filter(ctx, count, lambda m: m.author.id == member.id)

    @commands.hybrid_command(name="purgecontains")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def purge_contains(self, ctx: commands.Context, *, text: str) -> None:
        """Delete messages containing specific text."""
        await self._purge_filter(ctx, 100, lambda m: text.lower() in m.content.lower())

    @commands.hybrid_command(name="purgelinks")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def purge_links(self, ctx: commands.Context, count: int = 50) -> None:
        """Delete messages containing links."""
        import re
        url_re = re.compile(r"https?://", re.IGNORECASE)
        await self._purge_filter(ctx, count, lambda m: bool(url_re.search(m.content)))

    @commands.hybrid_command(name="purgeimages")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def purge_images(self, ctx: commands.Context, count: int = 50) -> None:
        """Delete messages with attachments."""
        await self._purge_filter(ctx, count, lambda m: len(m.attachments) > 0)

    @commands.hybrid_command(name="purgementions")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def purge_mentions(self, ctx: commands.Context, count: int = 50) -> None:
        """Delete messages with mentions."""
        await self._purge_filter(ctx, count, lambda m: len(m.mentions) > 0 or len(m.role_mentions) > 0)

    async def _purge_filter(self, ctx: commands.Context, limit: int, predicate) -> None:
        try:
            deleted = await ctx.channel.purge(limit=limit + 5, check=predicate)
            await ctx.send(embed=success("Purged", f"Deleted `{len(deleted)}` messages."), delete_after=5)
        except discord.Forbidden:
            await ctx.send(embed=error("Missing Permissions", "I cannot manage messages."))
        except discord.HTTPException as e:
            await ctx.send(embed=error("Error", f"```\n{e}\n```"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationCog(bot))
