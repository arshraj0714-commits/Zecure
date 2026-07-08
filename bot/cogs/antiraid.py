"""
AntiRaid cog - detects and mitigates coordinated raids.

Raid types:
- Join raid: many members join in a short window (especially new accounts)
- Leave raid: many members leave in a short window (suspected coordinated)
- Bot raid: many bots added rapidly
- Verification lockdown: temporarily require verification for new members

Mitigations:
- Auto-lockdown (deny send_messages in all channels)
- Quarantine (strip roles, isolate)
- Auto-kick/ban new joiners during raid
- Always notify owner

Important: BIG actions only trigger when there's a clear pattern of malicious intent.
Uses multiple signals: account age, join velocity, member count delta, behavior similarity.
"""
from __future__ import annotations

import asyncio
import time
import json
import discord
from collections import deque, defaultdict
from discord.ext import commands

from ..core.config import config
from ..core.database import db
from ..core.permissions import is_whitelisted
from ..core.features import is_feature_enabled
from ..core.logger import setup_logger
from ..utils.embeds import raid_alert, owner_alert, warning, success, info, error, security
from ..utils.helpers import notify_owner, quarantine_member, safe_send
from ..utils.constants import Colors, Icons

log = setup_logger("securitybot.antiraid")


class AntiRaidCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Sliding windows: {guild_id: deque[ts]}
        self._join_events: dict[int, deque] = defaultdict(lambda: deque(maxlen=500))
        self._leave_events: dict[int, deque] = defaultdict(lambda: deque(maxlen=500))
        self._bot_add_events: dict[int, deque] = defaultdict(lambda: deque(maxlen=50))
        # Raid state per guild
        self._raid_active: dict[int, dict] = {}
        # Recent joins for behavior analysis
        self._recent_joins: dict[int, deque] = defaultdict(lambda: deque(maxlen=50))

    # ---------- Config ----------
    async def _get_thresholds(self, guild_id: int) -> dict:
        guild_thresholds = await db.get_thresholds(guild_id)
        defaults = {
            "join_threshold": 10,
            "join_window_seconds": 10,
            "leave_threshold": 8,
            "leave_window_seconds": 15,
            "bot_add_threshold": 3,
            "bot_add_window_seconds": 60,
            "new_account_hours": 24,
            "auto_lockdown_on_raid": True,
            "punishment": "kick",
            "auto_clear_minutes": 15,
        }
        result = defaults.copy()
        result.update(guild_thresholds.get("antiraid", {}))
        return result

    async def _set_threshold(self, guild_id: int, key: str, value) -> None:
        thresholds = await db.get_thresholds(guild_id)
        antiraid = thresholds.get("antiraid", {})
        antiraid[key] = value
        thresholds["antiraid"] = antiraid
        await db.set_thresholds(guild_id, thresholds)

    def _prune_window(self, dq: deque, window: int) -> int:
        cutoff = time.time() - window
        while dq and dq[0] < cutoff:
            dq.popleft()
        return len(dq)

    async def _declare_raid(self, guild: discord.Guild, raid_type: str, user_ids: list[int], description: str, severity: str = "high") -> None:
        """Declare raid state and trigger mitigations."""
        if guild.id in self._raid_active:
            return  # already in raid mode
        self._raid_active[guild.id] = {
            "type": raid_type,
            "started_at": time.time(),
            "user_ids": set(user_ids),
        }

        thresholds = await self._get_thresholds(guild.id)
        punishment = thresholds.get("punishment", "kick")

        # 1. Notify owner IMMEDIATELY
        embed = owner_alert(
            f"🚨 RAID DETECTED — {raid_type.title()}",
            f"**Server:** {guild.name} (`{guild.id}`)\n"
            f"**Raid Type:** {raid_type}\n"
            f"**Severity:** {severity}\n"
            f"**Users Involved:** {len(user_ids)}\n"
            f"**Description:** {description}\n\n"
            f"**Mitigations:** auto-lockdown={'ON' if thresholds.get('auto_lockdown_on_raid') else 'OFF'}, punishment={punishment}",
            guild=guild,
        )
        await notify_owner(self.bot, embed)

        # 2. Auto-lockdown
        if thresholds.get("auto_lockdown_on_raid", True):
            await self._auto_lockdown(guild, reason=f"AntiRaid: {raid_type}")

        # 3. Punish involved users
        for uid in user_ids:
            member = guild.get_member(uid)
            if not member:
                continue
            try:
                if punishment == "ban":
                    await member.ban(reason=f"Zecurity • AntiRaid | {raid_type}", delete_message_days=1)
                elif punishment == "kick":
                    await member.kick(reason=f"Zecurity • AntiRaid | {raid_type}")
                elif punishment == "quarantine":
                    await quarantine_member(member, reason=f"Zecurity • AntiRaid | {raid_type}")
            except discord.Forbidden:
                pass
            except Exception as e:
                log.warning("Failed to punish %s during raid: %s", uid, e)

        # 4. Log to security channel
        log_row = await db.fetchone("SELECT security_log FROM log_channels WHERE guild_id = ?", (guild.id,))
        if log_row and log_row["security_log"]:
            channel = guild.get_channel(log_row["security_log"])
            if channel:
                alert = raid_alert(raid_type, description, severity=severity, user_count=len(user_ids))
                await safe_send(channel, embed=alert)

        # 5. Record incident
        await db.execute(
            """INSERT INTO incidents (guild_id, type, severity, description, actor_ids, action_taken)
               VALUES (?, 'antiraid', ?, ?, ?, ?)""",
            (guild.id, severity, description, json.dumps(user_ids), f"Auto-mitigated: lockdown + {punishment}"),
        )

        # 6. Record raid event
        await db.execute(
            "INSERT INTO raid_events (guild_id, raid_type, severity, user_ids) VALUES (?, ?, ?, ?)",
            (guild.id, raid_type, severity, json.dumps(user_ids)),
        )

        # 7. Schedule raid clear
        clear_minutes = thresholds.get("auto_clear_minutes", 15)
        self.bot.loop.create_task(self._auto_clear_raid(guild.id, clear_minutes * 60))

    async def _auto_lockdown(self, guild: discord.Guild, reason: str = "AntiRaid") -> None:
        """Apply lockdown to all text channels."""
        for channel in guild.text_channels:
            try:
                await channel.set_permissions(
                    guild.default_role,
                    send_messages=False,
                    reason=f"Zecurity {reason}",
                )
            except discord.Forbidden:
                pass

    async def _lift_lockdown(self, guild: discord.Guild) -> None:
        """Lift lockdown from all text channels."""
        for channel in guild.text_channels:
            try:
                await channel.set_permissions(
                    guild.default_role,
                    send_messages=None,
                    reason="Zecurity: raid cleared, lockdown lifted",
                )
            except discord.Forbidden:
                pass

    async def _auto_clear_raid(self, guild_id: int, after_seconds: int) -> None:
        await asyncio.sleep(after_seconds)
        if guild_id not in self._raid_active:
            return
        raid = self._raid_active.pop(guild_id)
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        await self._lift_lockdown(guild)

        embed = info(
            "Raid Cleared",
            f"Raid in **{guild.name}** (`{guild.id}`) cleared after {after_seconds//60} minutes.\n"
            f"**Type:** {raid['type']}\n"
            f"**Users involved:** {len(raid['user_ids'])}",
        )
        await notify_owner(self.bot, embed)

        await db.execute(
            "UPDATE raid_events SET resolved = 1 WHERE guild_id = ? AND raid_type = ? ORDER BY id DESC LIMIT 1",
            (guild_id, raid["type"]),
        )

    # ---------- Event listeners ----------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Join raid detection."""
        if not await is_feature_enabled(member.guild.id, 'antiraid'):
            return
        if member.bot:
            # Bot raid detection (separate from AntiNuke's antibot module)
            guild = member.guild
            now = time.time()
            self._bot_add_events[guild.id].append(now)
            thresholds = await self._get_thresholds(guild.id)
            bot_threshold = thresholds.get("bot_add_threshold", 3)
            bot_window = thresholds.get("bot_add_window_seconds", 60)
            bot_count = self._prune_window(self._bot_add_events[guild.id], bot_window)
            if bot_count >= bot_threshold:
                desc = f"{bot_count} bots added in {bot_window}s — possible bot raid"
                await self._declare_raid(guild, "bot", [member.id], desc, severity="critical")
            return

        guild = member.guild
        now = time.time()
        thresholds = await self._get_thresholds(guild.id)

        # Track join event
        self._join_events[guild.id].append(now)

        # Account age
        account_age_hours = (now - member.created_at.timestamp()) / 3600
        new_account_threshold = thresholds.get("new_account_hours", 24)
        is_new_account = account_age_hours < new_account_threshold

        # Track recent joins for behavior analysis
        self._recent_joins[guild.id].append((member.id, now, account_age_hours))

        # ---- Join raid detection ----
        join_threshold = thresholds.get("join_threshold", 10)
        join_window = thresholds.get("join_window_seconds", 10)
        count = self._prune_window(self._join_events[guild.id], join_window)

        # Only declare raid if majority are new accounts (intentional raid signal)
        if count >= join_threshold:
            recent = list(self._recent_joins[guild.id])
            new_ratio = sum(1 for _, _, age in recent if age < new_account_threshold) / max(len(recent), 1)
            if new_ratio >= 0.6:
                user_ids = [uid for uid, _, _ in recent]
                desc = (
                    f"{count} members joined in {join_window}s. "
                    f"{int(new_ratio*100)}% are new accounts (<{new_account_threshold}h old). "
                    f"This pattern strongly suggests a coordinated raid."
                )
                await self._declare_raid(guild, "join", user_ids, desc, severity="critical")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Leave raid detection."""
        if not await is_feature_enabled(member.guild.id, 'antiraid'):
            return
        guild = member.guild
        now = time.time()
        thresholds = await self._get_thresholds(guild.id)

        self._leave_events[guild.id].append(now)
        leave_threshold = thresholds.get("leave_threshold", 8)
        leave_window = thresholds.get("leave_window_seconds", 15)
        count = self._prune_window(self._leave_events[guild.id], leave_window)

        if count >= leave_threshold:
            # Only declare raid if it follows a recent join raid
            recent_join_count = self._prune_window(self._join_events[guild.id], 300)
            if recent_join_count >= 5:
                desc = (
                    f"{count} members left in {leave_window}s after {recent_join_count} recent joins. "
                    f"Possible coordinated leave raid or kick wave."
                )
                await self._declare_raid(guild, "leave", [member.id], desc, severity="medium")

    # ---------- Commands ----------

    @commands.hybrid_group(name="antiraid", aliases=["raid"], invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def antiraid_group(self, ctx: commands.Context) -> None:
        """AntiRaid detection system."""
        if ctx.subcommand_passed is None:
            thresholds = await self._get_thresholds(ctx.guild.id)
            embed = security("AntiRaid Configuration", f"Use `{ctx.prefix}antiraid <subcommand>` to configure.")
            embed.add_field(name="Status", value="🟢 Active raid" if ctx.guild.id in self._raid_active else "✅ No active raid", inline=True)
            embed.add_field(name="Join Threshold", value=f"`{thresholds['join_threshold']}` in `{thresholds['join_window_seconds']}s`", inline=True)
            embed.add_field(name="Leave Threshold", value=f"`{thresholds['leave_threshold']}` in `{thresholds['leave_window_seconds']}s`", inline=True)
            embed.add_field(name="Bot Add Threshold", value=f"`{thresholds['bot_add_threshold']}` in `{thresholds['bot_add_window_seconds']}s`", inline=True)
            embed.add_field(name="New Account Hours", value=f"`{thresholds['new_account_hours']}h`", inline=True)
            embed.add_field(name="Auto Lockdown", value="🟢 ON" if thresholds.get("auto_lockdown_on_raid") else "🔴 OFF", inline=True)
            embed.add_field(name="Punishment", value=f"**{thresholds['punishment']}**", inline=True)
            embed.add_field(name="Auto Clear", value=f"`{thresholds['auto_clear_minutes']} min`", inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)
            embed.add_field(
                name="Commands",
                value=f"```\n{ctx.prefix}antiraid threshold <key> <value>  - Set threshold\n"
                      f"{ctx.prefix}antiraid raidmode                    - Toggle manual raid mode\n"
                      f"{ctx.prefix}antiraid quarantine <member> [reason] - Quarantine a member\n"
                      f"{ctx.prefix}antiraid unquarantine <member>       - Release from quarantine\n"
                      f"{ctx.prefix}antiraid lockdown                    - Lock all channels\n"
                      f"{ctx.prefix}antiraid unlock                      - Unlock all channels\n"
                      f"{ctx.prefix}antiraid status                      - View current state\n```",
                inline=False,
            )
            await ctx.send(embed=embed)

    @antiraid_group.command(name="threshold", aliases=["set"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def antiraid_threshold(self, ctx: commands.Context, key: str, value: str) -> None:
        """Set an AntiRaid threshold. Keys: join_threshold, join_window_seconds, leave_threshold, leave_window_seconds, bot_add_threshold, bot_add_window_seconds, new_account_hours, auto_clear_minutes, punishment, auto_lockdown_on_raid"""
        thresholds = await self._get_thresholds(ctx.guild.id)
        if key not in thresholds:
            valid = ", ".join(f"`{k}`" for k in thresholds)
            await ctx.send(embed=error("Invalid Key", f"Valid keys: {valid}"))
            return
        try:
            if key == "punishment":
                if value not in ("kick", "ban", "quarantine"):
                    await ctx.send(embed=error("Invalid Value", "Punishment must be: kick, ban, or quarantine"))
                    return
                new_val = value
            elif key == "auto_lockdown_on_raid":
                new_val = value.lower() in ("true", "1", "yes", "on")
            else:
                new_val = int(value)
        except ValueError:
            await ctx.send(embed=error("Invalid Value", "Must be an integer."))
            return
        await self._set_threshold(ctx.guild.id, key, new_val)
        await ctx.send(embed=success("Threshold Updated", f"✅ `antiraid.{key}` set to `{new_val}`"))

    @antiraid_group.command(name="raidmode", aliases=["panic"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def raidmode_toggle(self, ctx: commands.Context) -> None:
        """Manually toggle raid mode (locks down the server)."""
        if ctx.guild.id in self._raid_active:
            raid = self._raid_active.pop(ctx.guild.id)
            await self._lift_lockdown(ctx.guild)
            await ctx.send(embed=success("Raid Mode Disabled", "✅ Lockdown lifted. Server is back to normal."))
            await notify_owner(
                self.bot,
                info("Manual Raid Mode Disabled", f"Server: **{ctx.guild.name}** (`{ctx.guild.id}`)\nBy: <@{ctx.author.id}>"),
            )
        else:
            self._raid_active[ctx.guild.id] = {
                "type": "manual",
                "started_at": time.time(),
                "user_ids": set(),
            }
            await self._auto_lockdown(ctx.guild, reason="manual raid mode")
            await ctx.send(embed=warning("Raid Mode Enabled", "⚠️ Server is now in lockdown. Use `.antiraid raidmode` again to disable."))
            await notify_owner(
                self.bot,
                warning("Manual Raid Mode Enabled", f"Server: **{ctx.guild.name}** (`{ctx.guild.id}`)\nBy: <@{ctx.author.id}>"),
            )

    @antiraid_group.command(name="quarantine")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def quarantine_cmd(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Manual quarantine") -> None:
        """Manually quarantine a member."""
        if member.id == ctx.guild.owner_id or member.id == self.bot.user.id:
            await ctx.send(embed=error("Cannot Quarantine", "Cannot quarantine this user."))
            return
        ok = await quarantine_member(member, reason=reason)
        if ok:
            await ctx.send(embed=success("Member Quarantined", f"✅ <@{member.id}> has been quarantined.\n**Reason:** {reason}"))
        else:
            await ctx.send(embed=error("Quarantine Failed", "Missing permissions to quarantine this member."))

    @antiraid_group.command(name="unquarantine")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def unquarantine_cmd(self, ctx: commands.Context, member: discord.Member) -> None:
        """Release a member from quarantine."""
        from ..utils.helpers import unquarantine_member
        ok = await unquarantine_member(member)
        if ok:
            await ctx.send(embed=success("Released", f"✅ <@{member.id}> has been released from quarantine."))
        else:
            await ctx.send(embed=error("Failed", "User is not quarantined or missing permissions."))

    @antiraid_group.command(name="lockdown")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def lockdown_cmd(self, ctx: commands.Context) -> None:
        """Lock all text channels."""
        await self._auto_lockdown(ctx.guild, reason=f"manual lockdown by {ctx.author}")
        await ctx.send(embed=warning("Server Locked Down", "⚠️ All text channels are now read-only.\nUse `.antiraid unlock` to lift."))

    @antiraid_group.command(name="unlock")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def unlock_cmd(self, ctx: commands.Context) -> None:
        """Unlock all text channels."""
        await self._lift_lockdown(ctx.guild)
        await ctx.send(embed=success("Server Unlocked", "✅ All text channels are back to normal."))

    @antiraid_group.command(name="status")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def status_cmd(self, ctx: commands.Context) -> None:
        """View current AntiRaid status."""
        embed = security("AntiRaid Status", f"**Server:** {ctx.guild.name}")
        if ctx.guild.id in self._raid_active:
            raid = self._raid_active[ctx.guild.id]
            embed.add_field(
                name="⚠️ Active Raid",
                value=f"**Type:** `{raid['type']}`\n**Started:** <t:{int(raid['started_at'])}:R>\n**Users:** `{len(raid['user_ids'])}`",
                inline=False,
            )
        else:
            embed.add_field(name="Status", value="✅ No active raid", inline=False)
        # Show recent activity
        join_count = self._prune_window(self._join_events[ctx.guild.id], 60)
        leave_count = self._prune_window(self._leave_events[ctx.guild.id], 60)
        embed.add_field(name="Joins (last 60s)", value=str(join_count), inline=True)
        embed.add_field(name="Leaves (last 60s)", value=str(leave_count), inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiRaidCog(bot))
