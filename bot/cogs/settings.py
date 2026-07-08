"""
Settings cog - central command for configuration.

Commands:
- !settings - view all settings
- !setprefix - set custom prefix
- !setlanguage - set bot language
- !addadminrole / !removeadminrole - manage admin roles
- !adminroles - list admin roles
- !addwhitelist / !removewhitelist / !whitelist - manage whitelist
- !resetsettings - reset all settings to defaults
- !botinfo - bot info & stats
- !help - custom help command
- !ping - latency check
"""
from __future__ import annotations

import discord
from discord.ext import commands
from typing import Optional, Union
import time

from ..core.config import config
from ..core.database import db
from ..core.permissions import get_permission_level, PermissionLevel, is_whitelisted
from ..core.logger import setup_logger
from ..core.i18n import t, get_available_languages
from ..utils.embeds import (
    success, error, warning, info, security, help_embed,
)
from ..utils.constants import Colors, Icons, FOOTER_TEXT
from ..utils.helpers import humanize_number
from ..core.whitelist import (
    add_to_whitelist, remove_from_whitelist, get_whitelist, clear_whitelist,
)

log = setup_logger("securitybot.settings")


class SettingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _ensure_guild_in_db(self, guild: discord.Guild) -> None:
        existing = await db.fetchone("SELECT 1 FROM guilds WHERE guild_id = ?", (guild.id,))
        if not existing:
            await db.execute(
                "INSERT INTO guilds (guild_id, prefix, language) VALUES (?, ?, ?)",
                (guild.id, config.default_prefix, config.default_language),
            )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._ensure_guild_in_db(guild)
        log.info("Joined guild: %s (%s)", guild.name, guild.id)
        # Notify owner
        from ..utils.helpers import notify_owner
        from ..utils.embeds import info as info_embed
        await notify_owner(
            self.bot,
            info_embed(
                "Added to New Server",
                f"**Server:** {guild.name} (`{guild.id}`)\n"
                f"**Owner:** <@{guild.owner_id}>\n"
                f"**Members:** {guild.member_count}",
            ),
        )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info("Removed from guild: %s (%s)", guild.name, guild.id)
        from ..utils.helpers import notify_owner
        from ..utils.embeds import warning as warning_embed
        await notify_owner(
            self.bot,
            warning_embed(
                "Removed from Server",
                f"**Server:** {guild.name} (`{guild.id}`)",
            ),
        )

    # ---------- Settings ----------

    @commands.hybrid_command(name="settings", aliases=["config"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def view_settings(self, ctx: commands.Context) -> None:
        """View all server settings."""
        await self._ensure_guild_in_db(ctx.guild)
        row = await db.fetchone("SELECT * FROM guilds WHERE guild_id = ?", (ctx.guild.id,))
        settings = await db.get_guild_settings(ctx.guild.id)
        thresholds = await db.get_thresholds(ctx.guild.id)

        embed = security("Server Settings", f"Configuration for **{ctx.guild.name}**")
        embed.add_field(name="Prefix", value=f"`{row['prefix']}`", inline=True)
        embed.add_field(name="Language", value=f"`{row['language']}`", inline=True)
        embed.add_field(name="Settings Keys", value=str(len(settings)), inline=True)
        embed.add_field(name="Threshold Keys", value=str(len(thresholds)), inline=True)
        embed.add_field(name="Added", value=f"<t:{row['added_at']}:R>", inline=True)

        # Counts
        wl_count = len(await get_whitelist(ctx.guild.id))
        admin_roles = await db.fetchall("SELECT 1 FROM admin_roles WHERE guild_id = ?", (ctx.guild.id,))
        embed.add_field(name="Whitelist Entries", value=str(wl_count), inline=True)
        embed.add_field(name="Admin Roles", value=str(len(admin_roles)), inline=True)
        embed.add_field(name="Bot Version", value="2.0.0", inline=True)

        # Feature status summary
        from ..core.features import get_all_feature_flags, ALL_FEATURES
        flags = await get_all_feature_flags(ctx.guild.id)
        on_count = sum(1 for v in flags.values() if v)
        total = len(ALL_FEATURES)
        summary = " ".join(f"{'🟢' if flags.get(f, True) else '🔴'} `{f}`" for f in ALL_FEATURES)
        embed.add_field(
            name=f"Protection Features ({on_count}/{total} ON)",
            value=summary[:1024],
            inline=False,
        )
        embed.add_field(
            name="Manage Features",
            value=f"Use `{row['prefix']}features` for details, `{row['prefix']}toggle <feature>` to switch.",
            inline=False,
        )

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="setlanguage", aliases=["setlang"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def set_language(self, ctx: commands.Context, language: str) -> None:
        available = get_available_languages()
        if language not in available:
            await ctx.send(embed=error("Invalid Language", f"Available: {', '.join(available)}"))
            return
        await self._ensure_guild_in_db(ctx.guild)
        await db.execute("UPDATE guilds SET language = ? WHERE guild_id = ?", (language, ctx.guild.id))
        await ctx.send(embed=success("Language Updated", f"Language set to `{language}`"))

    @commands.hybrid_command(name="languages")
    @commands.guild_only()
    async def list_languages(self, ctx: commands.Context) -> None:
        await ctx.send(embed=info("Available Languages", ", ".join(f"`{l}`" for l in get_available_languages())))

    # ---------- Admin roles ----------

    @commands.hybrid_command(name="addadminrole")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def add_admin_role(self, ctx: commands.Context, role: discord.Role) -> None:
        existing = await db.fetchone(
            "SELECT 1 FROM admin_roles WHERE guild_id = ? AND role_id = ?",
            (ctx.guild.id, role.id),
        )
        if existing:
            await ctx.send(embed=warning("Already Exists", "That role is already an admin role."))
            return
        await db.execute(
            "INSERT INTO admin_roles (guild_id, role_id, added_by) VALUES (?, ?, ?)",
            (ctx.guild.id, role.id, ctx.author.id),
        )
        await ctx.send(embed=success("Admin Role Added", f"<@&{role.id}> can now use bot commands."))

    @commands.hybrid_command(name="removeadminrole")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def remove_admin_role(self, ctx: commands.Context, role: discord.Role) -> None:
        cur = await db.execute(
            "DELETE FROM admin_roles WHERE guild_id = ? AND role_id = ?",
            (ctx.guild.id, role.id),
        )
        if cur.rowcount:
            await ctx.send(embed=success("Removed", f"<@&{role.id}> is no longer an admin role."))
        else:
            await ctx.send(embed=error("Not Found", "That role was not an admin role."))

    @commands.hybrid_command(name="adminroles")
    @commands.guild_only()
    async def list_admin_roles(self, ctx: commands.Context) -> None:
        rows = await db.fetchall("SELECT * FROM admin_roles WHERE guild_id = ?", (ctx.guild.id,))
        if not rows:
            await ctx.send(embed=info("No Admin Roles", "Use `!addadminrole @role` to add one."))
            return
        embed = info("Admin Roles", f"{len(rows)} roles with bot admin access:")
        for r in rows:
            embed.add_field(name=f"<@&{r['role_id']}>", value=f"Added by <@{r['added_by']}>", inline=True)
        await ctx.send(embed=embed)

    # ---------- Whitelist ----------

    @commands.command(name="addwhitelist", aliases=["wladd"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def add_whitelist(
        self,
        ctx: commands.Context,
        entity: Union[discord.Member, discord.Role, discord.TextChannel],
        *,
        reason: str = "No reason provided",
    ) -> None:
        if isinstance(entity, discord.Member):
            entity_type = "user"
        elif isinstance(entity, discord.Role):
            entity_type = "role"
        else:
            entity_type = "channel"
        added = await add_to_whitelist(ctx.guild.id, entity.id, entity_type, ctx.author.id, reason)
        if added:
            await ctx.send(embed=success("Whitelisted", f"{entity_type} `{entity}` added to whitelist.\n**Reason:** {reason}"))
        else:
            await ctx.send(embed=warning("Already Whitelisted", "That entity is already in the whitelist."))

    @commands.command(name="removewhitelist", aliases=["wlremove", "unwhitelist"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def remove_whitelist(
        self,
        ctx: commands.Context,
        entity: Union[discord.Member, discord.Role, discord.TextChannel],
    ) -> None:
        if isinstance(entity, discord.Member):
            entity_type = "user"
        elif isinstance(entity, discord.Role):
            entity_type = "role"
        else:
            entity_type = "channel"
        removed = await remove_from_whitelist(ctx.guild.id, entity.id, entity_type)
        if removed:
            await ctx.send(embed=success("Removed", f"{entity_type} `{entity}` removed from whitelist."))
        else:
            await ctx.send(embed=warning("Not Found", "That entity was not in the whitelist."))

    # Slash-compatible whitelist commands (accept specific types)
    @commands.hybrid_command(name="whitelistuser", description="Add a user to the whitelist")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def whitelist_user(self, ctx: commands.Context, member: discord.Member, reason: str = "No reason provided") -> None:
        added = await add_to_whitelist(ctx.guild.id, member.id, "user", ctx.author.id, reason)
        if added:
            await ctx.send(embed=success("Whitelisted", f"<@{member.id}> added to whitelist.\n**Reason:** {reason}"))
        else:
            await ctx.send(embed=warning("Already Whitelisted", "That user is already whitelisted."))

    @commands.hybrid_command(name="unwhitelistuser", description="Remove a user from the whitelist")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def unwhitelist_user(self, ctx: commands.Context, member: discord.Member) -> None:
        removed = await remove_from_whitelist(ctx.guild.id, member.id, "user")
        if removed:
            await ctx.send(embed=success("Removed", f"<@{member.id}> removed from whitelist."))
        else:
            await ctx.send(embed=warning("Not Found", "That user was not in the whitelist."))

    @commands.hybrid_command(name="whitelistrole", description="Add a role to the whitelist")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def whitelist_role(self, ctx: commands.Context, role: discord.Role, reason: str = "No reason provided") -> None:
        added = await add_to_whitelist(ctx.guild.id, role.id, "role", ctx.author.id, reason)
        if added:
            await ctx.send(embed=success("Whitelisted", f"<@&{role.id}> added to whitelist.\n**Reason:** {reason}"))
        else:
            await ctx.send(embed=warning("Already Whitelisted", "That role is already whitelisted."))

    @commands.hybrid_command(name="whitelistchannel", description="Add a channel to the whitelist")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def whitelist_channel(self, ctx: commands.Context, channel: discord.TextChannel, reason: str = "No reason provided") -> None:
        added = await add_to_whitelist(ctx.guild.id, channel.id, "channel", ctx.author.id, reason)
        if added:
            await ctx.send(embed=success("Whitelisted", f"<#{channel.id}> added to whitelist.\n**Reason:** {reason}"))
        else:
            await ctx.send(embed=warning("Already Whitelisted", "That channel is already whitelisted."))

    @commands.hybrid_command(name="whitelist", aliases=["wllist"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def list_whitelist(self, ctx: commands.Context, entity_type: Optional[str] = None) -> None:
        if entity_type and entity_type not in ("user", "role", "channel"):
            # Could be a member mention or just plain text - treat as list all
            entity_type = None
        rows = await get_whitelist(ctx.guild.id, entity_type)
        if not rows:
            await ctx.send(embed=info("Empty Whitelist", "No entities in whitelist."))
            return
        embed = info("Whitelist", f"{len(rows)} entities:")
        for r in rows[:20]:
            if r["entity_type"] == "user":
                mention = f"<@{r['entity_id']}>"
            elif r["entity_type"] == "role":
                mention = f"<@&{r['entity_id']}>"
            else:
                mention = f"<#{r['entity_id']}>"
            embed.add_field(
                name=f"{r['entity_type'].title()}: {mention}",
                value=f"By: <@{r['added_by']}>\nReason: {r['reason']}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="clearwhitelist")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def clear_whitelist_cmd(self, ctx: commands.Context) -> None:
        count = await clear_whitelist(ctx.guild.id)
        await ctx.send(embed=warning("Cleared", f"Removed {count} entries from whitelist."))

    # ---------- Feature Toggle ----------

    @commands.hybrid_command(name="features", aliases=["featurelist"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def list_features(self, ctx: commands.Context) -> None:
        """List all protection features and their on/off status."""
        from ..core.features import ALL_FEATURES, get_all_feature_flags
        flags = await get_all_feature_flags(ctx.guild.id)
        embed = discord.Embed(
            title=f"{Icons.SHIELD}  Protection Features",
            description="All features are **enabled by default**. Use `!toggle <feature>` to switch.",
            color=Colors.PRIMARY,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Zecurity • Built By Arsh")
        for feature_name, description in ALL_FEATURES.items():
            status = "🟢 ON" if flags.get(feature_name, True) else "🔴 OFF"
            embed.add_field(
                name=f"{status}  `{feature_name}`",
                value=description,
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="toggle")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def toggle_feature_cmd(self, ctx: commands.Context, feature: str) -> None:
        """Toggle a protection feature on/off. Example: !toggle antinuke"""
        from ..core.features import ALL_FEATURES, toggle_feature
        feature_lower = feature.lower()
        if feature_lower not in ALL_FEATURES:
            valid = ", ".join(f"`{f}`" for f in ALL_FEATURES)
            await ctx.send(embed=error("Invalid Feature", f"Unknown feature `{feature}`.\nValid features: {valid}"))
            return
        new_state = await toggle_feature(ctx.guild.id, feature_lower)
        status = "enabled 🟢" if new_state else "disabled 🔴"
        embed = success("Feature Toggled", f"`{feature_lower}` is now **{status}**.")
        embed.add_field(name="Description", value=ALL_FEATURES[feature_lower], inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="enable")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def enable_feature_cmd(self, ctx: commands.Context, feature: str) -> None:
        """Enable a protection feature. Example: !enable antiraid"""
        from ..core.features import ALL_FEATURES, set_feature_flag, is_feature_enabled
        feature_lower = feature.lower()
        if feature_lower not in ALL_FEATURES:
            valid = ", ".join(f"`{f}`" for f in ALL_FEATURES)
            await ctx.send(embed=error("Invalid Feature", f"Unknown feature `{feature}`.\nValid features: {valid}"))
            return
        if await is_feature_enabled(ctx.guild.id, feature_lower):
            await ctx.send(embed=info("Already Enabled", f"`{feature_lower}` is already enabled."))
            return
        await set_feature_flag(ctx.guild.id, feature_lower, True)
        await ctx.send(embed=success("Enabled", f"`{feature_lower}` is now **enabled** 🟢"))

    @commands.hybrid_command(name="disable")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def disable_feature_cmd(self, ctx: commands.Context, feature: str) -> None:
        """Disable a protection feature. Example: !disable automod"""
        from ..core.features import ALL_FEATURES, set_feature_flag, is_feature_enabled
        feature_lower = feature.lower()
        if feature_lower not in ALL_FEATURES:
            valid = ", ".join(f"`{f}`" for f in ALL_FEATURES)
            await ctx.send(embed=error("Invalid Feature", f"Unknown feature `{feature}`.\nValid features: {valid}"))
            return
        if not await is_feature_enabled(ctx.guild.id, feature_lower):
            await ctx.send(embed=info("Already Disabled", f"`{feature_lower}` is already disabled."))
            return
        await set_feature_flag(ctx.guild.id, feature_lower, False)
        await ctx.send(embed=warning("Disabled", f"`{feature_lower}` is now **disabled** 🔴\nUse `!enable {feature_lower}` to turn it back on."))

    # ---------- Reset ----------

    @commands.hybrid_command(name="resetsettings")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def reset_settings(self, ctx: commands.Context) -> None:
        await db.execute("DELETE FROM guild_settings WHERE guild_id = ?", (ctx.guild.id,))
        await db.execute("UPDATE guilds SET prefix = ?, language = ? WHERE guild_id = ?",
                         (config.default_prefix, config.default_language, ctx.guild.id))
        await ctx.send(embed=success("Reset", "All settings reset to defaults."))

    # ---------- Bot info ----------

    @commands.hybrid_command(name="botinfo", aliases=["about"])
    async def about_bot(self, ctx: commands.Context) -> None:
        """View bot information."""
        embed = security("Zecurity", "Enterprise-grade Discord security bot • Built By Arsh")
        embed.add_field(name="Version", value="`2.0.0`", inline=True)
        embed.add_field(name="Discord.py", value=f"`{discord.__version__}`", inline=True)
        embed.add_field(name="Python", value=f"`3.11`", inline=True)
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(name="Users", value=humanize_number(sum(g.member_count or 0 for g in self.bot.guilds)), inline=True)
        embed.add_field(name="Latency", value=f"`{self.bot.latency*1000:.0f}ms`", inline=True)
        if self.bot.start_time:
            embed.add_field(name="Uptime", value=f"<t:{int(self.bot.start_time.timestamp())}:R>", inline=True)
        embed.add_field(name="Owner", value=f"<@{config.owner_id}>", inline=True)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url if self.bot.user else None)
        await ctx.send(embed=embed)

    @commands.command(name="sync")
    @commands.guild_only()
    @commands.is_owner()
    async def sync_commands(self, ctx: commands.Context, scope: str = "guild") -> None:
        """Manually sync slash commands. Owner only. Usage: .sync [guild|global]"""
        msg = await ctx.send(embed=info("Syncing...", f"Syncing slash commands ({scope})..."))
        try:
            if scope == "global":
                synced = await self.bot.tree.sync()
                await msg.edit(embed=success("Synced", f"✅ Synced {len(synced)} commands globally.\nNote: Global sync may take up to 1 hour to appear."))
            else:
                # Guild sync (instant)
                self.bot.tree.copy_global_to(guild=ctx.guild)
                synced = await self.bot.tree.sync(guild=ctx.guild)
                await msg.edit(embed=success("Synced", f"✅ Synced {len(synced)} commands to this server (instant).\nSlash commands should now be visible."))
        except Exception as e:
            await msg.edit(embed=error("Sync Failed", f"```\n{e}\n```"))

    @commands.hybrid_command(name="ping")
    async def ping(self, ctx: commands.Context) -> None:
        latency_ms = self.bot.latency * 1000
        if latency_ms < 100:
            color = Colors.SUCCESS
            emoji = "🟢"
        elif latency_ms < 300:
            color = Colors.WARNING
            emoji = "🟡"
        else:
            color = Colors.DANGER
            emoji = "🔴"
        embed = discord.Embed(
            title=f"{emoji} Pong!",
            description=f"Latency: `{latency_ms:.0f}ms`",
            color=color,
        )
        await ctx.send(embed=embed)

    # ---------- Help ----------

    @commands.hybrid_command(name="help")
    async def help_command(self, ctx: commands.Context, *, category: Optional[str] = None) -> None:
        """Display bot help. Use .help <category> or .help <command>."""
        if not category:
            embed = help_embed("Zecurity Help", [
                {"name": "🛡️ AntiNuke", "description": f"`{ctx.prefix}antinuke` enable/disable/show/events/recover/whitelist/punishment, `{ctx.prefix}extraowner`"},
                {"name": "🚨 AntiRaid", "description": f"`{ctx.prefix}antiraid` threshold/raidmode/quarantine/lockdown/unlock/status"},
                {"name": "🤖 Automod", "description": f"`{ctx.prefix}automod` enable/disable/punishment/config/logging/ignore"},
                {"name": "💬 Antispam", "description": f"`{ctx.prefix}antispam`, `{ctx.prefix}antispamthreshold`, `{ctx.prefix}clearspam`"},
                {"name": "🔧 Antitamper", "description": f"`{ctx.prefix}antitamper`, `{ctx.prefix}protectrole`"},
                {"name": "🔨 Moderation", "description": f"`{ctx.prefix}ban`, `{ctx.prefix}kick`, `{ctx.prefix}mute`, `{ctx.prefix}warn`, `{ctx.prefix}purge`, `{ctx.prefix}nuke`, `{ctx.prefix}lock`, `{ctx.prefix}lockall`, `{ctx.prefix}setprefix`, `{ctx.prefix}avatar`"},
                {"name": "✅ Verification", "description": f"`{ctx.prefix}setupverification`, `{ctx.prefix}verification`, `{ctx.prefix}verify`"},
                {"name": "💾 Backup", "description": f"`{ctx.prefix}backup`, `{ctx.prefix}backups`, `{ctx.prefix}restorebackup`"},
                {"name": "📊 Analytics", "description": f"`{ctx.prefix}securityscore`, `{ctx.prefix}serverstats`, `{ctx.prefix}modstats`"},
                {"name": "📋 Logging", "description": f"`{ctx.prefix}setlog`, `{ctx.prefix}logchannels`, `{ctx.prefix}auditlog`"},
                {"name": "⏰ Scheduled", "description": f"`{ctx.prefix}schedule`, `{ctx.prefix}tasks`, `{ctx.prefix}deletetask`"},
                {"name": "⚙️ Settings", "description": f"`{ctx.prefix}settings`, `{ctx.prefix}setlanguage`, `{ctx.prefix}addadminrole`, `{ctx.prefix}addwhitelist`, `{ctx.prefix}features`, `{ctx.prefix}toggle`"},
                {"name": "ℹ️ Info", "description": f"`{ctx.prefix}botinfo`, `{ctx.prefix}ping`, `{ctx.prefix}help`, `{ctx.prefix}avatar`, `{ctx.prefix}serverinfo`, `{ctx.prefix}userinfo`, `{ctx.prefix}membercount`"},
                {"name": "⚡ Slash Commands", "description": f"`/ban`, `/kick`, `/mute`, `/unmute`, `/warn`, `/purge`, `/nuke`, `/lock`, `/unlock`, `/slowmode`, `/ping`, `/botinfo`, `/userinfo`, `/serverinfo`, `/avatar`, `/securityscore`, `/antinuke`, `/automod`"},
            ])
            embed.add_field(
                name="Need more help?",
                value=(
                    f"Use `{ctx.prefix}help <category>` for category details.\n"
                    f"Use `{ctx.prefix}help <command>` for a single command.\n"
                    f"Examples: `{ctx.prefix}help antinuke`, `{ctx.prefix}help antinuke`"
                ),
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        category_lower = category.lower().lstrip(ctx.prefix)

        # First: try to look up a single command (by name or alias)
        cmd = self.bot.get_command(category_lower)
        if cmd is not None:
            embed = self._build_command_help(ctx, cmd)
            await ctx.send(embed=embed)
            return

        # Second: try category lookup
        commands_map = {
            "antinuke": [
                {"name": ".antinuke enable", "description": "Enable Anti-Nuke (owner only)"},
                {"name": ".antinuke disable", "description": "Disable Anti-Nuke (owner only)"},
                {"name": ".antinuke show", "description": "View configuration"},
                {"name": ".antinuke events [module] [on|off]", "description": "View or toggle modules"},
                {"name": ".antinuke recover <channels|roles> <name>", "description": "Bulk delete by name"},
                {"name": ".antinuke whitelist add <user>", "description": "Whitelist a user (max 10)"},
                {"name": ".antinuke whitelist remove <user>", "description": "Remove from whitelist"},
                {"name": ".antinuke whitelist show", "description": "Show whitelisted users"},
                {"name": ".antinuke whitelist reset", "description": "Clear whitelist"},
                {"name": ".antinuke punishment set <Ban|Kick|Strip>", "description": "Set punishment"},
                {"name": ".antinuke punishment show", "description": "Show current punishment"},
                {"name": ".extraowner add <user>", "description": "Add extra-owner (owner only, max 3)"},
                {"name": ".extraowner remove <user>", "description": "Remove extra-owner"},
                {"name": ".extraowner show", "description": "Show extra-owners"},
            ],
            "antiraid": [
                {"name": ".antiraid", "description": "View AntiRaid configuration"},
                {"name": ".antiraid threshold <key> <value>", "description": "Set a threshold (join_threshold, leave_threshold, etc.)"},
                {"name": ".antiraid raidmode", "description": "Toggle manual raid mode (locks down server)"},
                {"name": ".antiraid quarantine <member> [reason]", "description": "Quarantine a member"},
                {"name": ".antiraid unquarantine <member>", "description": "Release from quarantine"},
                {"name": ".antiraid lockdown", "description": "Lock all text channels"},
                {"name": ".antiraid unlock", "description": "Unlock all text channels"},
                {"name": ".antiraid status", "description": "View current raid status"},
            ],
            "automod": [
                {"name": ".automod enable", "description": "Enable AutoMod (all 6 modules)"},
                {"name": ".automod disable", "description": "Disable AutoMod"},
                {"name": ".automod punishment <event> <Mute|Kick|Ban>", "description": "Set punishment per event"},
                {"name": ".automod config", "description": "View configuration"},
                {"name": ".automod logging <#channel>", "description": "Set log channel"},
                {"name": ".automod ignore channel <#channel>", "description": "Ignore a channel"},
                {"name": ".automod ignore role <@role>", "description": "Ignore a role"},
                {"name": ".automod ignore show", "description": "Show ignored"},
                {"name": ".automod ignore reset", "description": "Reset ignored"},
            ],
            "moderation": [
                {"name": ".ban <member> [reason]", "description": "Ban a member"},
                {"name": ".unban <user_id>", "description": "Unban a user"},
                {"name": ".unbanall", "description": "Unban all users"},
                {"name": ".kick <member> [reason]", "description": "Kick a member"},
                {"name": ".mute <member> [duration] [reason]", "description": "Timeout a member"},
                {"name": ".unmute <member>", "description": "Remove timeout"},
                {"name": ".warn <member> <reason>", "description": "Warn a member"},
                {"name": ".warnings [member]", "description": "View warnings"},
                {"name": ".purge <count>", "description": "Bulk delete messages"},
                {"name": ".purgebots [count]", "description": "Delete bot messages"},
                {"name": ".purgehumans [count]", "description": "Delete human messages"},
                {"name": ".purgeuser <member> [count]", "description": "Delete user's messages"},
                {"name": ".purgecontains <text>", "description": "Delete messages containing text"},
                {"name": ".purgelinks [count]", "description": "Delete messages with links"},
                {"name": ".purgeimages [count]", "description": "Delete messages with attachments"},
                {"name": ".purgementions [count]", "description": "Delete messages with mentions"},
                {"name": ".nuke", "description": "Clone & delete current channel"},
                {"name": ".lock [channel]", "description": "Lock a channel"},
                {"name": ".unlock [channel]", "description": "Unlock a channel"},
                {"name": ".lockall", "description": "Lock all channels"},
                {"name": ".unlockall", "description": "Unlock all channels"},
                {"name": ".hideall", "description": "Hide all channels from @everyone"},
                {"name": ".unhideall", "description": "Unhide all channels"},
                {"name": ".slowmode <seconds>", "description": "Set slowmode"},
                {"name": ".setprefix <prefix>", "description": "Set custom prefix"},
                {"name": ".roleall <role> [all|humans|bots]", "description": "Add role to all members"},
                {"name": ".steal <emoji> [name]", "description": "Add emoji to server"},
                {"name": ".userinfo [member]", "description": "View user info"},
                {"name": ".serverinfo", "description": "View server info"},
                {"name": ".avatar [member]", "description": "View user avatar"},
                {"name": ".servericon", "description": "View server icon"},
                {"name": ".membercount", "description": "View member count"},
                {"name": ".roleinfo <role>", "description": "View role info"},
                {"name": ".inrole <role>", "description": "List members in role"},
                {"name": ".history <member>", "description": "View user history"},
            ],
            "antispam": [
                {"name": ".antispam", "description": "View AntiSpam config"},
                {"name": ".antispamthreshold <key> <value>", "description": "Set a threshold"},
                {"name": ".clearspam <member>", "description": "Clear violations"},
            ],
            "antitamper": [
                {"name": ".antitamper", "description": "View AntiTamper config"},
                {"name": ".protectrole <role>", "description": "Protect a role"},
            ],
            "settings": [
                {"name": ".settings", "description": "View all settings"},
                {"name": ".setlanguage <lang>", "description": "Change language"},
                {"name": ".addadminrole <role>", "description": "Add admin role"},
                {"name": ".removeadminrole <role>", "description": "Remove admin role"},
                {"name": ".addwhitelist <entity> [reason]", "description": "Add to whitelist"},
                {"name": ".removewhitelist <entity>", "description": "Remove from whitelist"},
                {"name": ".features", "description": "List all features and their on/off status"},
                {"name": ".toggle <feature>", "description": "Toggle a feature on/off"},
                {"name": ".enable <feature>", "description": "Enable a feature"},
                {"name": ".disable <feature>", "description": "Disable a feature"},
                {"name": ".resetsettings", "description": "Reset to defaults"},
            ],
            "verification": [
                {"name": ".setupverification <channel> <role> [method] [min_age]", "description": "Setup verification"},
                {"name": ".verification", "description": "View verification config"},
                {"name": ".disableverification", "description": "Disable verification"},
                {"name": ".verify", "description": "Manually verify"},
            ],
            "backup": [
                {"name": ".backup", "description": "Create server backup"},
                {"name": ".backups", "description": "List backups"},
                {"name": ".restorebackup <id>", "description": "Restore a backup"},
                {"name": ".confirmrestore <id>", "description": "Confirm restore"},
                {"name": ".deletebackup <id>", "description": "Delete a backup"},
            ],
        }
        cmds = commands_map.get(category_lower)
        if cmds:
            embed = help_embed(f"Help — {category.title()}", cmds)
            await ctx.send(embed=embed)
            return

        # Third: not found — give a helpful error
        await ctx.send(embed=error(
            "Not Found",
            f"No category or command named `{category}`.\n"
            f"Use `{ctx.prefix}help` to see all categories, or `{ctx.prefix}help <command>` for a single command.",
        ))

    def _build_command_help(self, ctx: commands.Context, cmd: commands.Command) -> discord.Embed:
        """Build a help embed for a single command."""
        from ..utils.constants import Colors, Icons
        # Build signature
        signature = f"{ctx.prefix}{cmd.qualified_name}"
        if cmd.signature:
            signature += f" {cmd.signature}"
        # Description
        desc = cmd.help or cmd.brief or "No description available."
        embed = discord.Embed(
            title=f"{Icons.GEAR}  Command: `{cmd.qualified_name}`",
            description=f"```\n{signature}\n```\n{desc}",
            color=Colors.PRIMARY,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Zecurity • Built By Arsh")
        # Aliases
        if cmd.aliases:
            aliases = ", ".join(f"`{ctx.prefix}{a}`" for a in cmd.aliases)
            embed.add_field(name="Aliases", value=aliases, inline=False)
        # Cooldown
        if hasattr(cmd, "_buckets") and cmd._buckets._cooldown:
            cd = cmd._buckets._cooldown
            embed.add_field(name="Cooldown", value=f"{cd.rate} per {cd.per}s", inline=True)
        # Subcommands (if group)
        if isinstance(cmd, commands.Group):
            subcmds = "\n".join(f"`{ctx.prefix}{cmd.qualified_name} {s.name}` — {s.short_doc or 'no help'}" for s in cmd.commands)
            if subcmds:
                embed.add_field(name="Subcommands", value=subcmds[:1024], inline=False)
        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SettingsCog(bot))
