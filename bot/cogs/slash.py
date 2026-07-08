"""
Slash commands - mirrors the most common prefix commands.
Every command here is also available as a prefix command (.command).
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from ..core.config import config
from ..core.database import db
from ..core.permissions import is_admin
from ..utils.embeds import success, error, warning, info, security
from ..utils.helpers import parse_duration, format_duration, can_act_on
from ..utils.constants import Colors, Icons


class SlashCog(commands.Cog):
    """Slash command tree."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ---------- Moderation ----------

    @app_commands.command(name="ban", description="Ban a member")
    @app_commands.describe(member="Member to ban", reason="Reason for the ban")
    @app_commands.default_permissions(ban_members=True)
    async def slash_ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided") -> None:
        if not interaction.user.guild_permissions.ban_members:
            await interaction.response.send_message(embed=error("Missing Permissions", "You need Ban Members permission."), ephemeral=True)
            return
        if not can_act_on(self.bot, interaction.guild, interaction.user, member):
            await interaction.response.send_message(embed=error("Cannot Ban", "You don't have permission to ban this user."), ephemeral=True)
            return
        try:
            await member.ban(reason=f"{interaction.user}: {reason}", delete_message_days=1)
            await interaction.response.send_message(embed=success("Banned", f"<@{member.id}> has been banned.\n**Reason:** {reason}"))
        except discord.Forbidden:
            await interaction.response.send_message(embed=error("Missing Permissions", "I cannot ban this user."), ephemeral=True)

    @app_commands.command(name="kick", description="Kick a member")
    @app_commands.describe(member="Member to kick", reason="Reason for the kick")
    @app_commands.default_permissions(kick_members=True)
    async def slash_kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided") -> None:
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message(embed=error("Missing Permissions", "You need Kick Members permission."), ephemeral=True)
            return
        if not can_act_on(self.bot, interaction.guild, interaction.user, member):
            await interaction.response.send_message(embed=error("Cannot Kick", "You don't have permission to kick this user."), ephemeral=True)
            return
        try:
            await member.kick(reason=f"{interaction.user}: {reason}")
            await interaction.response.send_message(embed=success("Kicked", f"<@{member.id}> has been kicked.\n**Reason:** {reason}"))
        except discord.Forbidden:
            await interaction.response.send_message(embed=error("Missing Permissions", "I cannot kick this user."), ephemeral=True)

    @app_commands.command(name="mute", description="Timeout a member")
    @app_commands.describe(member="Member to mute", duration="Duration (e.g. 10m, 1h, 1d)", reason="Reason")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_mute(self, interaction: discord.Interaction, member: discord.Member, duration: str = "10m", reason: str = "No reason provided") -> None:
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message(embed=error("Missing Permissions", "You need Timeout Members permission."), ephemeral=True)
            return
        seconds = parse_duration(duration) or 600
        try:
            until = discord.utils.utcnow() + discord.utils.timedelta(seconds=seconds)
            await member.timeout(until, reason=f"{interaction.user}: {reason}")
            await interaction.response.send_message(embed=success("Muted", f"<@{member.id}> muted for {format_duration(seconds)}.\n**Reason:** {reason}"))
        except discord.Forbidden:
            await interaction.response.send_message(embed=error("Missing Permissions", "I cannot mute this user."), ephemeral=True)

    @app_commands.command(name="unmute", description="Remove timeout from a member")
    @app_commands.describe(member="Member to unmute")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_unmute(self, interaction: discord.Interaction, member: discord.Member) -> None:
        try:
            await member.timeout(None, reason=f"Unmuted by {interaction.user}")
            await interaction.response.send_message(embed=success("Unmuted", f"<@{member.id}> has been unmuted."))
        except discord.Forbidden:
            await interaction.response.send_message(embed=error("Missing Permissions", "I cannot unmute this user."), ephemeral=True)

    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.describe(member="Member to warn", reason="Reason")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_warn(self, interaction: discord.Interaction, member: discord.Member, reason: str) -> None:
        await db.execute(
            """INSERT INTO user_history (guild_id, user_id, action, moderator_id, reason)
               VALUES (?, ?, 'warn', ?, ?)""",
            (interaction.guild.id, member.id, interaction.user.id, reason),
        )
        await interaction.response.send_message(embed=success("Warning Issued", f"<@{member.id}> has been warned.\n**Reason:** {reason}"))

    @app_commands.command(name="purge", description="Delete messages in bulk")
    @app_commands.describe(count="Number of messages to delete (1-100)")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_purge(self, interaction: discord.Interaction, count: int) -> None:
        if count < 1 or count > 100:
            await interaction.response.send_message(embed=error("Invalid", "Count must be 1-100."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=count)
            await interaction.followup.send(embed=success("Purged", f"Deleted `{len(deleted)}` messages."), ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(embed=error("Missing Permissions", "I cannot manage messages."), ephemeral=True)

    @app_commands.command(name="nuke", description="Clone and delete the current channel")
    @app_commands.default_permissions(manage_channels=True)
    async def slash_nuke(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            new_channel = await interaction.channel.clone(reason=f"Nuke by {interaction.user}")
            await new_channel.edit(position=interaction.channel.position)
            await interaction.channel.delete(reason=f"Nuke by {interaction.user}")
            await new_channel.send(embed=success("Channel Nuked", f"💥 Nuked by {interaction.user.mention}"))
        except discord.Forbidden:
            await interaction.followup.send(embed=error("Missing Permissions", "I need Manage Channels permission."))

    @app_commands.command(name="lock", description="Lock a channel")
    @app_commands.default_permissions(manage_channels=True)
    async def slash_lock(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        channel = channel or interaction.channel
        try:
            await channel.set_permissions(interaction.guild.default_role, send_messages=False, reason=f"Lock by {interaction.user}")
            await interaction.response.send_message(embed=warning("Channel Locked", f"<#{channel.id}> is now read-only."))
        except discord.Forbidden:
            await interaction.response.send_message(embed=error("Missing Permissions", "I cannot manage this channel."), ephemeral=True)

    @app_commands.command(name="unlock", description="Unlock a channel")
    @app_commands.default_permissions(manage_channels=True)
    async def slash_unlock(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        channel = channel or interaction.channel
        try:
            await channel.set_permissions(interaction.guild.default_role, send_messages=None, reason=f"Unlock by {interaction.user}")
            await interaction.response.send_message(embed=success("Channel Unlocked", f"<#{channel.id}> is no longer read-only."))
        except discord.Forbidden:
            await interaction.response.send_message(embed=error("Missing Permissions", "I cannot manage this channel."), ephemeral=True)

    @app_commands.command(name="slowmode", description="Set slowmode for the channel")
    @app_commands.describe(seconds="Slowmode in seconds (0 to disable)")
    @app_commands.default_permissions(manage_channels=True)
    async def slash_slowmode(self, interaction: discord.Interaction, seconds: int) -> None:
        if seconds < 0 or seconds > 21600:
            await interaction.response.send_message(embed=error("Invalid", "Slowmode must be 0-21600 seconds."), ephemeral=True)
            return
        try:
            await interaction.channel.edit(slowmode_delay=seconds, reason=f"Slowmode set by {interaction.user}")
            await interaction.response.send_message(embed=success("Slowmode Updated", f"Slowmode is now `{seconds}s`."))
        except discord.Forbidden:
            await interaction.response.send_message(embed=error("Missing Permissions", "Cannot edit this channel."), ephemeral=True)

    # ---------- Info ----------

    @app_commands.command(name="ping", description="Check bot latency")
    async def slash_ping(self, interaction: discord.Interaction) -> None:
        latency = self.bot.latency * 1000
        await interaction.response.send_message(embed=info("Pong!", f"Latency: `{latency:.0f}ms`"))

    @app_commands.command(name="botinfo", description="View bot information")
    async def slash_botinfo(self, interaction: discord.Interaction) -> None:
        embed = security("Zecurity", "Enterprise-grade Discord security bot • Built By Arsh")
        embed.add_field(name="Version", value="`2.0.0`", inline=True)
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(name="Latency", value=f"`{self.bot.latency*1000:.0f}ms`", inline=True)
        embed.add_field(name="Owner", value=f"<@{config.owner_id}>", inline=True)
        embed.add_field(name="Built By", value="**Arsh**", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="userinfo", description="View user information")
    @app_commands.describe(member="Member to view (defaults to you)")
    async def slash_userinfo(self, interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
        member = member or interaction.user
        embed = discord.Embed(
            title=f"User Info — {member.display_name}",
            color=Colors.PRIMARY,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=f"<@{member.id}>", inline=True)
        embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
        if member.joined_at:
            embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Roles", value=str(len(member.roles)), inline=True)
        embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
        embed.add_field(name="Bot", value="Yes" if member.bot else "No", inline=True)
        embed.set_footer(text="Zecurity • Built By Arsh")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverinfo", description="View server information")
    async def slash_serverinfo(self, interaction: discord.Interaction) -> None:
        g = interaction.guild
        embed = info(f"Server Info — {g.name}")
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="Owner", value=f"<@{g.owner_id}>", inline=True)
        embed.add_field(name="Created", value=f"<t:{int(g.created_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Members", value=str(g.member_count), inline=True)
        embed.add_field(name="Channels", value=str(len(g.channels)), inline=True)
        embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
        embed.add_field(name="Boosts", value=f"Lvl {g.premium_tier} ({g.premium_subscription_count})", inline=True)
        embed.add_field(name="Verification Level", value=str(g.verification_level), inline=True)
        embed.set_footer(text="Zecurity • Built By Arsh")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="avatar", description="View a user's avatar")
    @app_commands.describe(member="Member to view (defaults to you)")
    async def slash_avatar(self, interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
        member = member or interaction.user
        embed = discord.Embed(
            title=f"Avatar — {member.display_name}",
            color=Colors.PRIMARY,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_image(url=member.display_avatar.url)
        embed.set_footer(text="Zecurity • Built By Arsh")
        await interaction.response.send_message(embed=embed)

    # ---------- Security ----------

    @app_commands.command(name="securityscore", description="View the server's security score")
    @app_commands.default_permissions(administrator=True)
    async def slash_score(self, interaction: discord.Interaction) -> None:
        from ..core.security_score import calculate_security_score
        from ..utils.embeds import score_embed
        await interaction.response.defer()
        score, factors = await calculate_security_score(interaction.guild)
        await interaction.followup.send(embed=score_embed(score, factors))

    @app_commands.command(name="antinuke", description="View AntiNuke status")
    @app_commands.default_permissions(administrator=True)
    async def slash_antinuke(self, interaction: discord.Interaction) -> None:
        from ..cogs.antinuke import AntiNukeCog, ALL_MODULES
        import json
        cog = self.bot.get_cog("AntiNukeCog")
        if not cog:
            await interaction.response.send_message(embed=error("Error", "AntiNuke cog not loaded."), ephemeral=True)
            return
        cfg = await cog._ensure_config(interaction.guild.id)
        owners = json.loads(cfg.get("owners", "[]"))
        whitelisted = json.loads(cfg.get("whitelisted", "[]"))
        embed = discord.Embed(
            title=f"Anti-Nuke Configuration — {interaction.guild.name}",
            color=Colors.ANTINUKE,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Status", value="🟢 Enabled" if cfg["status"] == "on" else "🔴 Disabled", inline=True)
        embed.add_field(name="Punishment", value=f"**{cfg['punishment']}**", inline=True)
        embed.add_field(name="Auto Recovery", value="✅ Enabled", inline=True)
        embed.add_field(name="Extra Owners", value=str(len(owners)), inline=True)
        embed.add_field(name="Whitelisted", value=str(len(whitelisted)), inline=True)
        modules_str = ""
        for m in ALL_MODULES:
            status = "🟢" if cfg.get(m, "on") == "on" else "🔴"
            modules_str += f"{status} `{m}`\n"
        embed.add_field(name="Modules", value=modules_str, inline=False)
        embed.set_footer(text="Zecurity • Built By Arsh")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="automod", description="View AutoMod status")
    @app_commands.default_permissions(administrator=True)
    async def slash_automod(self, interaction: discord.Interaction) -> None:
        row = await db.fetchone("SELECT * FROM automod WHERE guild_id = ?", (interaction.guild.id,))
        enabled = bool(row and row["enabled"])
        punishments = await db.fetchall(
            "SELECT event, punishment FROM automod_punishments WHERE guild_id = ?",
            (interaction.guild.id,),
        )
        pun_map = {p["event"]: p["punishment"] for p in punishments}
        embed = discord.Embed(
            title=f"AutoMod Configuration — {interaction.guild.name}",
            color=Colors.AUTOMOD,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Status", value="🟢 Enabled" if enabled else "🔴 Disabled", inline=True)
        modules_str = ""
        for ev in ["Anti spam", "Anti caps", "Anti link", "Anti invites", "Anti mass mention", "Anti emoji spam"]:
            status = "🟢" if ev in pun_map else "🔴"
            pun = pun_map.get(ev, "Mute")
            modules_str += f"{status} `{ev}` → **{pun}**\n"
        embed.add_field(name="Modules", value=modules_str, inline=False)
        embed.set_footer(text="Zecurity • Built By Arsh")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SlashCog(bot))
