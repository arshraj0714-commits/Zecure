"""
AntiNuke cog - Cypher-style exact replica.

Behavior matches the original Cypher bot:
- Master toggle (off by default; enabled via .antinuke enable)
- 11 sub-modules, all ON by default once antinuke is enabled
- Owner + extra-owners + whitelisted users all fully exempt
- 3 punishment types: Ban / Kick / Strip (3 variants)
- Auto-recovery: clone deleted channels, revert updates, unban victims, delete spam webhooks/roles/channels/emojis
- Reason prefix: "Cypher • Security | <EventName>"
- Threshold = 1 (every event fires on first action, matching Cypher)
"""
from __future__ import annotations

import asyncio
import discord
from discord.ext import commands
from discord import AuditLogAction
import json
import time

from ..core.config import config
from ..core.database import db
from ..core.logger import setup_logger
from ..utils.embeds import antinuke_embed, antinuke_error, success, warning
from ..utils.constants import Colors, Icons, SECURITY_REASON_PREFIX
from ..utils.helpers import notify_owner, safe_send

log = setup_logger("securitybot.antinuke")

# All 11 AntiNuke modules
ALL_MODULES = [
    "antiban", "antibot", "antichannel", "antiemoji", "antiguild",
    "antikick", "antiping", "antiprune", "antirole", "antiweb", "antimember",
]


class AntiNukeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ---------- DB helpers ----------
    async def _get_config(self, guild_id: int) -> dict:
        row = await db.fetchone("SELECT * FROM antinuke_config WHERE guild_id = ?", (guild_id,))
        if not row:
            return {
                "status": "off",
                "punishment": "Ban",
                "owners": "[]",
                "whitelisted": "[]",
                **{m: "on" for m in ALL_MODULES},
            }
        return dict(row)

    async def _ensure_config(self, guild_id: int) -> dict:
        cfg = await self._get_config(guild_id)
        if not await db.fetchone("SELECT 1 FROM antinuke_config WHERE guild_id = ?", (guild_id,)):
            await db._ensure_guild_exists(guild_id)
            await db.execute(
                """INSERT OR IGNORE INTO antinuke_config (guild_id) VALUES (?)""",
                (guild_id,),
            )
        return cfg

    async def _is_exempt(self, member: discord.Member, guild: discord.Guild) -> bool:
        """Check if member is exempt: bot, guild owner, extra-owner, or whitelisted."""
        if member.id == self.bot.user.id:
            return True
        if member.id == guild.owner_id:
            return True
        cfg = await self._get_config(guild.id)
        owners = json.loads(cfg.get("owners", "[]"))
        whitelisted = json.loads(cfg.get("whitelisted", "[]"))
        if str(member.id) in owners:
            return True
        if str(member.id) in whitelisted:
            return True
        return False

    async def _is_module_enabled(self, guild_id: int, module: str) -> bool:
        cfg = await self._get_config(guild_id)
        if cfg.get("status") != "on":
            return False
        return cfg.get(module, "on") == "on"

    async def _punish(self, guild: discord.Guild, member: discord.Member, event_name: str) -> None:
        """Apply the configured punishment. Reason = 'Cypher • Security | <EventName>'"""
        cfg = await self._get_config(guild.id)
        punishment = cfg.get("punishment", "Ban")
        reason = f"{SECURITY_REASON_PREFIX} | {event_name}"

        try:
            if punishment == "Ban":
                await guild.ban(member, reason=reason, delete_message_days=1)
            elif punishment == "Kick":
                await member.kick(reason=reason)
            elif punishment == "Strip":
                # Determine which strip variant based on event name (matches Cypher inconsistency)
                if event_name in ("AntiBan", "AntiUnban", "AntiWebhookCreate", "AntiWebhookDelete",
                                   "AntiGuild", "AntiEmojiCreate", "AntiEmojiDelete", "AntiEmojiUpdate"):
                    # Remove only administrator-permission roles
                    roles_to_remove = [r for r in member.roles if r.permissions.administrator]
                elif event_name in ("AntiKick", "AntiPing", "AntiPrune"):
                    # Remove ALL roles
                    roles_to_remove = list(member.roles)
                else:
                    # AntiBot / AntiChannel* / AntiRole* / AntiMember: remove roles below bot's top role
                    roles_to_remove = [
                        r for r in member.roles
                        if r != guild.default_role and r.position < guild.me.top_role.position
                    ]
                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove, reason=reason)
        except discord.Forbidden:
            log.warning("Cannot punish %s in %s - missing permissions", member.id, guild.id)
        except Exception as e:
            log.exception("Punish failed: %s", e)

    async def _log_event(self, guild: discord.Guild, member: discord.Member, event_name: str, description: str) -> None:
        """Send to log channels and owner."""
        embed = discord.Embed(
            title=f"{Icons.SHIELD}  Anti-Nuke: {event_name}",
            description=description,
            color=Colors.ANTINUKE,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Actor", value=f"<@{member.id}> (`{member.id}`)", inline=True)
        embed.add_field(name="Action", value=event_name, inline=True)
        embed.set_footer(text="Cypher • Security")

        # Send to configured log channels
        log_row = await db.fetchone("SELECT * FROM antinuke_logs WHERE guild_id = ?", (guild.id,))
        sent = False
        if log_row:
            for col in ("channel_logs", "mod_logs", "guild_logs", "role_logs"):
                channel_id = log_row[col]
                if channel_id:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        await safe_send(channel, embed=embed)
                        sent = True
                        break

        # Also send to security_log if configured
        sec_row = await db.fetchone("SELECT security_log FROM log_channels WHERE guild_id = ?", (guild.id,))
        if sec_row and sec_row["security_log"]:
            channel = guild.get_channel(sec_row["security_log"])
            if channel:
                await safe_send(channel, embed=embed)

        # Record incident
        await db.execute(
            """INSERT INTO incidents (guild_id, type, severity, description, actor_ids, action_taken)
               VALUES (?, 'antinuke', 'critical', ?, ?, ?)""",
            (guild.id, f"AntiNuke: {event_name} - {description}", str(member.id), f"Auto-punished"),
        )

    # ---------- Event listeners (all 11 modules) ----------

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        """AntiBan: punish the banner + unban the victim."""
        if not await self._is_module_enabled(guild.id, "antiban"):
            return
        try:
            async for entry in guild.audit_logs(limit=1, action=AuditLogAction.ban):
                if entry.target.id != user.id:
                    continue
                member = entry.user
                if await self._is_exempt(member, guild):
                    return
                await self._punish(guild, member, "AntiBan")
                await self._log_event(guild, member, "AntiBan",
                    f"Banned <@{user.id}> (`{user.id}`)")
                # Reverse the ban
                try:
                    await guild.unban(user, reason=f"{SECURITY_REASON_PREFIX} | AntiBan reversal")
                except discord.Forbidden:
                    pass
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        """AntiUnban: punish the unbanner + re-ban the user."""
        if not await self._is_module_enabled(guild.id, "antiban"):
            return
        try:
            async for entry in guild.audit_logs(limit=1, action=AuditLogAction.unban):
                if entry.target.id != user.id:
                    continue
                member = entry.user
                if await self._is_exempt(member, guild):
                    return
                await self._punish(guild, member, "AntiUnban")
                await self._log_event(guild, member, "AntiUnban",
                    f"Unbanned <@{user.id}> (`{user.id}`)")
                # Re-ban the user
                try:
                    await guild.ban(user, reason=f"{SECURITY_REASON_PREFIX} | AntiUnban reversal", delete_message_days=0)
                except discord.Forbidden:
                    pass
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """AntiKick + AntiPrune."""
        guild = member.guild
        # AntiKick
        if await self._is_module_enabled(guild.id, "antikick"):
            try:
                async for entry in guild.audit_logs(limit=2, action=AuditLogAction.kick):
                    if entry.target.id != member.id:
                        continue
                    actor = entry.user
                    if await self._is_exempt(actor, guild):
                        return
                    await self._punish(guild, actor, "AntiKick")
                    await self._log_event(guild, actor, "AntiKick",
                        f"Kicked <@{member.id}> (`{member.id}`)")
                    break
            except discord.Forbidden:
                pass

        # AntiPrune
        if await self._is_module_enabled(guild.id, "antiprune"):
            try:
                async for entry in guild.audit_logs(limit=1, action=AuditLogAction.member_prune):
                    actor = entry.user
                    if await self._is_exempt(actor, guild):
                        return
                    # Check time window (within 30s, matching Cypher)
                    if entry.created_at and (discord.utils.utcnow() - entry.created_at).total_seconds() > 30:
                        return
                    await self._punish(guild, actor, "AntiPrune")
                    await self._log_event(guild, actor, "AntiPrune",
                        f"Pruned members (included <@{member.id}>)")
            except discord.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """AntiBot: detect bot additions."""
        if not member.bot:
            return
        if not await self._is_module_enabled(member.guild.id, "antibot"):
            return
        try:
            async for entry in member.guild.audit_logs(limit=1, action=AuditLogAction.bot_add):
                if entry.target.id != member.id:
                    continue
                actor = entry.user
                if await self._is_exempt(actor, member.guild):
                    return
                await self._punish(member.guild, actor, "AntiBot")
                await self._log_event(member.guild, actor, "AntiBot",
                    f"Added bot <@{member.id}> (`{member.id}`)")
                # Ban the added bot
                try:
                    await member.ban(reason=f"{SECURITY_REASON_PREFIX} | AntiBot - unauthorized bot", delete_message_days=0)
                except discord.Forbidden:
                    pass
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        """AntiChannelCreate: punish + delete the new channel."""
        if not await self._is_module_enabled(channel.guild.id, "antichannel"):
            return
        try:
            async for entry in channel.guild.audit_logs(limit=1, action=AuditLogAction.channel_create):
                if entry.target.id != channel.id:
                    continue
                member = entry.user
                if await self._is_exempt(member, channel.guild):
                    return
                await self._punish(channel.guild, member, "AntiChannelCreate")
                await self._log_event(channel.guild, member, "AntiChannelCreate",
                    f"Created channel #{channel.name} (`{channel.id}`)")
                try:
                    await channel.delete(reason=f"{SECURITY_REASON_PREFIX} | AntiChannelCreate reversal")
                except discord.Forbidden:
                    pass
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """AntiChannelDelete: punish + clone the deleted channel."""
        if not await self._is_module_enabled(channel.guild.id, "antichannel"):
            return
        try:
            async for entry in channel.guild.audit_logs(limit=1, action=AuditLogAction.channel_delete):
                if entry.target.id != channel.id:
                    continue
                member = entry.user
                if await self._is_exempt(member, channel.guild):
                    return
                await self._punish(channel.guild, member, "AntiChannelDelete")
                await self._log_event(channel.guild, member, "AntiChannelDelete",
                    f"Deleted channel #{channel.name} (`{channel.id}`)")
                # Clone the channel
                try:
                    await channel.clone(reason=f"{SECURITY_REASON_PREFIX} | AntiChannelDelete recovery")
                except discord.Forbidden:
                    pass
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        """AntiChannelUpdate: punish + revert to before."""
        if not await self._is_module_enabled(after.guild.id, "antichannel"):
            return
        try:
            async for entry in after.guild.audit_logs(limit=1, action=AuditLogAction.channel_update):
                if entry.target.id != after.id:
                    continue
                member = entry.user
                if await self._is_exempt(member, after.guild):
                    return
                await self._punish(after.guild, member, "AntiChannelUpdate")
                await self._log_event(after.guild, member, "AntiChannelUpdate",
                    f"Updated channel #{after.name} (`{after.id}`)")
                # Revert
                try:
                    kwargs = {}
                    if hasattr(before, "name") and before.name != after.name:
                        kwargs["name"] = before.name
                    if hasattr(before, "topic") and before.topic != after.topic:
                        kwargs["topic"] = before.topic
                    if hasattr(before, "nsfw") and before.nsfw != after.nsfw:
                        kwargs["nsfw"] = before.nsfw
                    if hasattr(before, "slowmode_delay") and before.slowmode_delay != after.slowmode_delay:
                        kwargs["slowmode_delay"] = before.slowmode_delay
                    if hasattr(before, "category") and before.category != after.category:
                        kwargs["category"] = before.category
                    if kwargs:
                        await after.edit(**kwargs, reason=f"{SECURITY_REASON_PREFIX} | AntiChannelUpdate reversal")
                except discord.Forbidden:
                    pass
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        """AntiRoleCreate: punish + delete the new role."""
        if not await self._is_module_enabled(role.guild.id, "antirole"):
            return
        try:
            async for entry in role.guild.audit_logs(limit=5, action=AuditLogAction.role_create):
                if entry.target.id != role.id:
                    continue
                # Time window (10s, matching Cypher)
                if entry.created_at and (discord.utils.utcnow() - entry.created_at).total_seconds() > 10:
                    continue
                member = entry.user
                if await self._is_exempt(member, role.guild):
                    return
                await self._punish(role.guild, member, "AntiRoleCreate")
                await self._log_event(role.guild, member, "AntiRoleCreate",
                    f"Created role @{role.name} (`{role.id}`)")
                try:
                    await role.delete(reason=f"{SECURITY_REASON_PREFIX} | AntiRoleCreate reversal")
                except discord.Forbidden:
                    pass
                break
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        """AntiRoleDelete: punish + recreate the deleted role."""
        if not await self._is_module_enabled(role.guild.id, "antirole"):
            return
        try:
            async for entry in role.guild.audit_logs(limit=1, action=AuditLogAction.role_delete):
                if entry.target.id != role.id:
                    continue
                member = entry.user
                if await self._is_exempt(member, role.guild):
                    return
                await self._punish(role.guild, member, "AntiRoleDelete")
                await self._log_event(role.guild, member, "AntiRoleDelete",
                    f"Deleted role @{role.name} (`{role.id}`)")
                # Recreate the role
                try:
                    await role.guild.create_role(
                        name=role.name,
                        permissions=role.permissions,
                        color=role.color,
                        hoist=role.hoist,
                        mentionable=role.mentionable,
                        reason=f"{SECURITY_REASON_PREFIX} | AntiRoleDelete recovery",
                    )
                except discord.Forbidden:
                    pass
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        """AntiRoleUpdate: punish + revert to before."""
        if not await self._is_module_enabled(after.guild.id, "antirole"):
            return
        try:
            async for entry in after.guild.audit_logs(limit=1, action=AuditLogAction.role_update):
                if entry.target.id != after.id:
                    continue
                member = entry.user
                if await self._is_exempt(member, after.guild):
                    return
                await self._punish(after.guild, member, "AntiRoleUpdate")
                await self._log_event(after.guild, member, "AntiRoleUpdate",
                    f"Updated role @{after.name} (`{after.id}`)")
                # Revert
                try:
                    kwargs = {}
                    if before.name != after.name:
                        kwargs["name"] = before.name
                    if before.permissions != after.permissions:
                        kwargs["permissions"] = before.permissions
                    if before.color != after.color:
                        kwargs["color"] = before.color
                    if before.hoist != after.hoist:
                        kwargs["hoist"] = before.hoist
                    if before.mentionable != after.mentionable:
                        kwargs["mentionable"] = before.mentionable
                    if kwargs:
                        await after.edit(**kwargs, reason=f"{SECURITY_REASON_PREFIX} | AntiRoleUpdate reversal")
                except discord.Forbidden:
                    pass
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.TextChannel) -> None:
        """AntiWebhookCreate + AntiWebhookDelete."""
        guild = channel.guild
        # Try create first
        if await self._is_module_enabled(guild.id, "antiweb"):
            try:
                async for entry in guild.audit_logs(limit=1, action=AuditLogAction.webhook_create):
                    member = entry.user
                    if await self._is_exempt(member, guild):
                        return
                    await self._punish(guild, member, "AntiWebhookCreate")
                    await self._log_event(guild, member, "AntiWebhookCreate",
                        f"Created webhook in #{channel.name}")
                    # Delete the webhook
                    try:
                        webhooks = await channel.webhooks()
                        for wh in webhooks:
                            if wh.creator and wh.creator.id == member.id:
                                await wh.delete(reason=f"{SECURITY_REASON_PREFIX} | AntiWebhookCreate reversal")
                    except discord.Forbidden:
                        pass
                    return
            except discord.Forbidden:
                pass
            # Try delete
            try:
                async for entry in guild.audit_logs(limit=1, action=AuditLogAction.webhook_delete):
                    member = entry.user
                    if await self._is_exempt(member, guild):
                        return
                    await self._punish(guild, member, "AntiWebhookDelete")
                    await self._log_event(guild, member, "AntiWebhookDelete",
                        f"Deleted webhook in #{channel.name}")
                    return
            except discord.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild: discord.Guild, before, after) -> None:
        """AntiEmojiCreate / AntiEmojiDelete / AntiEmojiUpdate."""
        if not await self._is_module_enabled(guild.id, "antiemoji"):
            return
        before_ids = {e.id for e in before}
        after_ids = {e.id for e in after}
        added = after_ids - before_ids
        removed = before_ids - after_ids

        if added:
            # Emoji create
            try:
                async for entry in guild.audit_logs(limit=1, action=AuditLogAction.emoji_create):
                    member = entry.user
                    if await self._is_exempt(member, guild):
                        return
                    new_emoji = next((e for e in after if e.id in added), None)
                    await self._punish(guild, member, "AntiEmojiCreate")
                    await self._log_event(guild, member, "AntiEmojiCreate",
                        f"Created emoji {new_emoji.name if new_emoji else '?'}")
                    # Delete the new emoji
                    if new_emoji:
                        try:
                            await new_emoji.delete(reason=f"{SECURITY_REASON_PREFIX} | AntiEmojiCreate reversal")
                        except discord.Forbidden:
                            pass
                    return
            except discord.Forbidden:
                pass

        if removed:
            # Emoji delete
            try:
                async for entry in guild.audit_logs(limit=1, action=AuditLogAction.emoji_delete):
                    member = entry.user
                    if await self._is_exempt(member, guild):
                        return
                    deleted_emoji = next((e for e in before if e.id in removed), None)
                    await self._punish(guild, member, "AntiEmojiDelete")
                    await self._log_event(guild, member, "AntiEmojiDelete",
                        f"Deleted emoji {deleted_emoji.name if deleted_emoji else '?'}")
                    # Recreate the deleted emoji
                    if deleted_emoji:
                        try:
                            await guild.create_custom_emoji(
                                name=deleted_emoji.name,
                                image=await deleted_emoji.read(),
                                reason=f"{SECURITY_REASON_PREFIX} | AntiEmojiDelete recovery",
                            )
                        except (discord.Forbidden, discord.HTTPException):
                            pass
                    return
            except discord.Forbidden:
                pass

        # Emoji update (name change)
        if not added and not removed:
            try:
                async for entry in guild.audit_logs(limit=1, action=AuditLogAction.emoji_update):
                    member = entry.user
                    if await self._is_exempt(member, guild):
                        return
                    await self._punish(guild, member, "AntiEmojiUpdate")
                    await self._log_event(guild, member, "AntiEmojiUpdate",
                        f"Updated emoji")
                    # Revert name
                    for b in before:
                        for a in after:
                            if b.id == a.id and b.name != a.name:
                                try:
                                    await a.edit(name=b.name, reason=f"{SECURITY_REASON_PREFIX} | AntiEmojiUpdate reversal")
                                except discord.Forbidden:
                                    pass
                    return
            except discord.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        """AntiGuild: punish + revert guild settings."""
        if not await self._is_module_enabled(after.id, "antiguild"):
            return
        # Skip if nothing visible changed
        if (before.name == after.name and before.icon == after.icon
            and before.description == after.description
            and before.verification_level == after.verification_level):
            return
        try:
            async for entry in after.audit_logs(limit=1, action=AuditLogAction.guild_update):
                member = entry.user
                if await self._is_exempt(member, after):
                    return
                await self._punish(after, member, "AntiGuild")
                await self._log_event(after, member, "AntiGuild",
                    f"Updated server settings")
                # Revert guild settings
                try:
                    kwargs = {}
                    if before.name != after.name:
                        kwargs["name"] = before.name
                    if before.description != after.description:
                        kwargs["description"] = before.description
                    if before.verification_level != after.verification_level:
                        kwargs["verification_level"] = before.verification_level
                    if kwargs:
                        await after.edit(**kwargs, reason=f"{SECURITY_REASON_PREFIX} | AntiGuild reversal")
                except discord.Forbidden:
                    pass
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """AntiPing: detect @everyone/@here pings."""
        if not message.guild or message.author.bot:
            return
        if not message.mention_everyone:
            return
        if not await self._is_module_enabled(message.guild.id, "antiping"):
            return
        if await self._is_exempt(message.author, message.guild):
            return

        member = message.author
        await self._punish(message.guild, member, "AntiPing")
        await self._log_event(message.guild, member, "AntiPing",
            f"Pinged @everyone/@here in #{message.channel.name}")

        # Delete the author's recent messages containing mass pings
        try:
            async for hist in message.channel.history(limit=20):
                if hist.author.id == member.id and ("@everyone" in hist.content or "@here" in hist.content):
                    try:
                        await hist.delete()
                    except (discord.Forbidden, discord.HTTPException):
                        pass
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ---------- Commands (Cypher-style) ----------

    @commands.hybrid_group(name="antinuke", aliases=["anti", "security", "protection"], invoke_without_command=True)
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def antinuke_group(self, ctx: commands.Context) -> None:
        """Anti-Nuke protection system."""
        if ctx.subcommand_passed is None:
            embed = antinuke_embed(
                "Anti-Nuke Commands",
                f"```\n{ctx.prefix}antinuke enable       - Enable Anti-Nuke\n"
                f"{ctx.prefix}antinuke disable      - Disable Anti-Nuke\n"
                f"{ctx.prefix}antinuke show         - View configuration\n"
                f"{ctx.prefix}antinuke events       - Toggle modules\n"
                f"{ctx.prefix}antinuke recover      - Bulk cleanup\n"
                f"{ctx.prefix}antinuke whitelist    - Manage whitelist\n"
                f"{ctx.prefix}antinuke punishment   - Set punishment\n"
                f"{ctx.prefix}extraowner            - Manage extra owners\n```",
            )
            await ctx.send(embed=embed)

    @antinuke_group.command(name="enable", aliases=["on"])
    @commands.guild_only()
    async def antinuke_enable(self, ctx: commands.Context) -> None:
        """Enable Anti-Nuke. Guild owner only."""
        if ctx.author.id != ctx.guild.owner_id and ctx.author.id != config.owner_id:
            await ctx.send(embed=antinuke_error("Permission Denied", "Only the server owner can enable Anti-Nuke."))
            return
        await self._ensure_config(ctx.guild.id)
        await db.execute("UPDATE antinuke_config SET status = 'on' WHERE guild_id = ?", (ctx.guild.id,))
        embed = antinuke_embed("Anti-Nuke Enabled", f"✅ Anti-Nuke is now **enabled** for **{ctx.guild.name}**.\nAll 11 protection modules are active.")
        await ctx.send(embed=embed)

    @antinuke_group.command(name="disable", aliases=["off"])
    @commands.guild_only()
    async def antinuke_disable(self, ctx: commands.Context) -> None:
        """Disable Anti-Nuke. Guild owner only."""
        if ctx.author.id != ctx.guild.owner_id and ctx.author.id != config.owner_id:
            await ctx.send(embed=antinuke_error("Permission Denied", "Only the server owner can disable Anti-Nuke."))
            return
        await self._ensure_config(ctx.guild.id)
        await db.execute("UPDATE antinuke_config SET status = 'off' WHERE guild_id = ?", (ctx.guild.id,))
        embed = antinuke_embed("Anti-Nuke Disabled", f"⚠️ Anti-Nuke is now **disabled** for **{ctx.guild.name}**.")
        await ctx.send(embed=embed)

    @antinuke_group.command(name="show", aliases=["config"])
    @commands.guild_only()
    async def antinuke_show(self, ctx: commands.Context) -> None:
        """View Anti-Nuke configuration. Guild owner only."""
        if ctx.author.id != ctx.guild.owner_id and ctx.author.id != config.owner_id:
            await ctx.send(embed=antinuke_error("Permission Denied", "Only the server owner can view this."))
            return
        cfg = await self._ensure_config(ctx.guild.id)
        owners = json.loads(cfg.get("owners", "[]"))
        whitelisted = json.loads(cfg.get("whitelisted", "[]"))

        embed = antinuke_embed(f"Anti-Nuke Configuration — {ctx.guild.name}")
        embed.add_field(name="Status", value="🟢 Enabled" if cfg["status"] == "on" else "🔴 Disabled", inline=True)
        embed.add_field(name="Punishment", value=f"**{cfg['punishment']}**", inline=True)
        embed.add_field(name="Auto Recovery", value="✅ Enabled", inline=True)
        embed.add_field(name="Extra Owners", value=str(len(owners)), inline=True)
        embed.add_field(name="Whitelisted", value=str(len(whitelisted)), inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        # Module status
        modules_str = ""
        for m in ALL_MODULES:
            status = "🟢" if cfg.get(m, "on") == "on" else "🔴"
            modules_str += f"{status} `{m}`\n"
        embed.add_field(name="Modules", value=modules_str, inline=False)

        embed.add_field(
            name="Manage",
            value=f"Use `{ctx.prefix}antinuke events` to toggle modules\n"
                  f"Use `{ctx.prefix}antinuke punishment set` to change punishment\n"
                  f"Use `{ctx.prefix}antinuke whitelist add <user>` to whitelist",
            inline=False,
        )
        await ctx.send(embed=embed)

    @antinuke_group.command(name="events", aliases=["module"])
    @commands.guild_only()
    async def antinuke_events(self, ctx: commands.Context, module: str = None, state: str = None) -> None:
        """Toggle a specific module. Example: .antinuke events antiban off"""
        cfg = await self._ensure_config(ctx.guild.id)
        owners = json.loads(cfg.get("owners", "[]"))
        # Permission: guild owner or extra-owner
        if ctx.author.id != ctx.guild.owner_id and ctx.author.id != config.owner_id and str(ctx.author.id) not in owners:
            await ctx.send(embed=antinuke_error("Permission Denied", "Only the server owner or extra-owners can toggle modules."))
            return

        if module is None:
            # List modules with status
            embed = antinuke_embed("Anti-Nuke Modules", f"Use `{ctx.prefix}antinuke events <module> <on|off>` to toggle.")
            for m in ALL_MODULES:
                status = "🟢 ON" if cfg.get(m, "on") == "on" else "🔴 OFF"
                embed.add_field(name=m, value=status, inline=True)
            await ctx.send(embed=embed)
            return

        module_lower = module.lower()
        if module_lower not in ALL_MODULES:
            await ctx.send(embed=antinuke_error("Invalid Module", f"Valid modules: {', '.join(ALL_MODULES)}"))
            return

        if state is None:
            # Toggle
            new_state = "off" if cfg.get(module_lower, "on") == "on" else "on"
        else:
            new_state = "on" if state.lower() in ("on", "1", "true", "yes") else "off"

        await db.execute(
            f"UPDATE antinuke_config SET {module_lower} = ? WHERE guild_id = ?",
            (new_state, ctx.guild.id),
        )
        emoji = "🟢" if new_state == "on" else "🔴"
        await ctx.send(embed=antinuke_embed("Module Updated", f"{emoji} `{module_lower}` is now **{new_state}**"))

    @antinuke_group.command(name="recover", aliases=["clear"])
    @commands.guild_only()
    async def antinuke_recover(self, ctx: commands.Context, target: str, *, name: str) -> None:
        """Bulk delete channels or roles by name. Example: .antinuke recover channels nuked"""
        cfg = await self._ensure_config(ctx.guild.id)
        owners = json.loads(cfg.get("owners", "[]"))
        if ctx.author.id != ctx.guild.owner_id and ctx.author.id != config.owner_id and str(ctx.author.id) not in owners:
            await ctx.send(embed=antinuke_error("Permission Denied", "Only the server owner or extra-owners can recover."))
            return

        target_lower = target.lower()
        if target_lower not in ("channels", "roles"):
            await ctx.send(embed=antinuke_error("Invalid Target", "Use `channels` or `roles`."))
            return

        deleted_count = 0
        if target_lower == "channels":
            for channel in ctx.guild.channels:
                if name.lower() in channel.name.lower():
                    try:
                        await channel.delete(reason=f"{SECURITY_REASON_PREFIX} | Recovery by {ctx.author}")
                        deleted_count += 1
                    except discord.Forbidden:
                        pass
        else:
            for role in ctx.guild.roles:
                if name.lower() in role.name.lower() and not role.is_default() and not role.is_bot_managed():
                    try:
                        await role.delete(reason=f"{SECURITY_REASON_PREFIX} | Recovery by {ctx.author}")
                        deleted_count += 1
                    except discord.Forbidden:
                        pass

        await ctx.send(embed=antinuke_embed("Recovery Complete", f"Deleted **{deleted_count}** {target_lower} matching `{name}`"))

    # ---- Whitelist subcommands ----

    @antinuke_group.group(name="whitelist", aliases=["wl"], invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def antinuke_whitelist(self, ctx: commands.Context) -> None:
        """Manage Anti-Nuke whitelist."""
        if ctx.subcommand_passed is None:
            embed = antinuke_embed(
                "Anti-Nuke Whitelist Commands",
                f"```\n{ctx.prefix}antinuke whitelist add <user>\n"
                f"{ctx.prefix}antinuke whitelist remove <user>\n"
                f"{ctx.prefix}antinuke whitelist show\n"
                f"{ctx.prefix}antinuke whitelist reset\n```",
            )
            await ctx.send(embed=embed)

    @antinuke_whitelist.command(name="add")
    @commands.guild_only()
    async def whitelist_add(self, ctx: commands.Context, member: discord.Member) -> None:
        """Add a user to the Anti-Nuke whitelist."""
        cfg = await self._ensure_config(ctx.guild.id)
        owners = json.loads(cfg.get("owners", "[]"))
        if ctx.author.id != ctx.guild.owner_id and ctx.author.id != config.owner_id and str(ctx.author.id) not in owners:
            await ctx.send(embed=antinuke_error("Permission Denied", "Only the server owner or extra-owners can manage the whitelist."))
            return

        whitelisted = json.loads(cfg.get("whitelisted", "[]"))
        if str(member.id) in whitelisted:
            await ctx.send(embed=antinuke_error("Already Whitelisted", f"<@{member.id}> is already whitelisted."))
            return
        if len(whitelisted) >= 10:
            await ctx.send(embed=antinuke_error("Limit Reached", "Maximum 10 whitelisted users. Remove one first."))
            return

        whitelisted.append(str(member.id))
        await db.execute(
            "UPDATE antinuke_config SET whitelisted = ? WHERE guild_id = ?",
            (json.dumps(whitelisted), ctx.guild.id),
        )
        await ctx.send(embed=antinuke_embed("User Whitelisted", f"✅ <@{member.id}> (`{member.id}`) is now whitelisted.\nTotal: **{len(whitelisted)}/10**"))

    @antinuke_whitelist.command(name="remove", aliases=["rmv"])
    @commands.guild_only()
    async def whitelist_remove(self, ctx: commands.Context, member: discord.Member) -> None:
        """Remove a user from the Anti-Nuke whitelist."""
        cfg = await self._ensure_config(ctx.guild.id)
        owners = json.loads(cfg.get("owners", "[]"))
        if ctx.author.id != ctx.guild.owner_id and ctx.author.id != config.owner_id and str(ctx.author.id) not in owners:
            await ctx.send(embed=antinuke_error("Permission Denied", "Only the server owner or extra-owners can manage the whitelist."))
            return

        whitelisted = json.loads(cfg.get("whitelisted", "[]"))
        if str(member.id) not in whitelisted:
            await ctx.send(embed=antinuke_error("Not Found", f"<@{member.id}> is not whitelisted."))
            return

        whitelisted.remove(str(member.id))
        await db.execute(
            "UPDATE antinuke_config SET whitelisted = ? WHERE guild_id = ?",
            (json.dumps(whitelisted), ctx.guild.id),
        )
        await ctx.send(embed=antinuke_embed("User Removed", f"✅ <@{member.id}> removed from whitelist."))

    @antinuke_whitelist.command(name="show")
    @commands.guild_only()
    async def whitelist_show(self, ctx: commands.Context) -> None:
        """Show all whitelisted users."""
        cfg = await self._ensure_config(ctx.guild.id)
        whitelisted = json.loads(cfg.get("whitelisted", "[]"))
        if not whitelisted:
            await ctx.send(embed=antinuke_embed("Whitelist Empty", "No users are whitelisted."))
            return
        embed = antinuke_embed(f"Anti-Nuke Whitelist — {len(whitelisted)}/10")
        for uid in whitelisted:
            embed.add_field(name=f"`{uid}`", value=f"<@{uid}>", inline=False)
        await ctx.send(embed=embed)

    @antinuke_whitelist.command(name="reset", aliases=["clear"])
    @commands.guild_only()
    async def whitelist_reset(self, ctx: commands.Context) -> None:
        """Clear the entire whitelist."""
        cfg = await self._ensure_config(ctx.guild.id)
        owners = json.loads(cfg.get("owners", "[]"))
        if ctx.author.id != ctx.guild.owner_id and ctx.author.id != config.owner_id and str(ctx.author.id) not in owners:
            await ctx.send(embed=antinuke_error("Permission Denied", "Only the server owner or extra-owners can reset the whitelist."))
            return
        await db.execute(
            "UPDATE antinuke_config SET whitelisted = '[]' WHERE guild_id = ?",
            (ctx.guild.id,),
        )
        await ctx.send(embed=antinuke_embed("Whitelist Cleared", "All whitelisted users have been removed."))

    # ---- Punishment subcommands ----

    @antinuke_group.group(name="punishment", invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def antinuke_punishment(self, ctx: commands.Context) -> None:
        """Manage Anti-Nuke punishment."""
        if ctx.subcommand_passed is None:
            embed = antinuke_embed(
                "Anti-Nuke Punishment Commands",
                f"```\n{ctx.prefix}antinuke punishment set <Ban|Kick|Strip>\n"
                f"{ctx.prefix}antinuke punishment show\n```",
            )
            await ctx.send(embed=embed)

    @antinuke_punishment.command(name="set")
    @commands.guild_only()
    async def punishment_set(self, ctx: commands.Context, punishment: str) -> None:
        """Set the Anti-Nuke punishment. Options: Ban, Kick, Strip."""
        cfg = await self._ensure_config(ctx.guild.id)
        owners = json.loads(cfg.get("owners", "[]"))
        if ctx.author.id != ctx.guild.owner_id and ctx.author.id != config.owner_id and str(ctx.author.id) not in owners:
            await ctx.send(embed=antinuke_error("Permission Denied", "Only the server owner or extra-owners can change the punishment."))
            return

        punishment_lower = punishment.capitalize()
        if punishment_lower not in ("Ban", "Kick", "Strip"):
            await ctx.send(embed=antinuke_error("Invalid Punishment", "Options: `Ban`, `Kick`, or `Strip`."))
            return

        await db.execute(
            "UPDATE antinuke_config SET punishment = ? WHERE guild_id = ?",
            (punishment_lower, ctx.guild.id),
        )
        await ctx.send(embed=antinuke_embed("Punishment Updated", f"✅ Anti-Nuke punishment set to **{punishment_lower}**"))

    @antinuke_punishment.command(name="show")
    @commands.guild_only()
    async def punishment_show(self, ctx: commands.Context) -> None:
        """Show the current Anti-Nuke punishment."""
        cfg = await self._ensure_config(ctx.guild.id)
        await ctx.send(embed=antinuke_embed("Current Punishment", f"Anti-Nuke punishment: **{cfg['punishment']}**"))

    # ---- Extra-owner (separate group, like Cypher) ----

    @commands.hybrid_group(name="extraowner", aliases=["own"], invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def extraowner_group(self, ctx: commands.Context) -> None:
        """Manage extra-owners for Anti-Nuke."""
        if ctx.subcommand_passed is None:
            embed = antinuke_embed(
                "Extra-Owner Commands",
                f"```\n{ctx.prefix}extraowner add <user>\n"
                f"{ctx.prefix}extraowner remove <user>\n"
                f"{ctx.prefix}extraowner show\n```",
            )
            await ctx.send(embed=embed)

    @extraowner_group.command(name="add")
    @commands.guild_only()
    async def extraowner_add(self, ctx: commands.Context, member: discord.Member) -> None:
        """Add an extra-owner. Guild owner only. Max 3."""
        if ctx.author.id != ctx.guild.owner_id and ctx.author.id != config.owner_id:
            await ctx.send(embed=antinuke_error("Permission Denied", "Only the server owner can add extra-owners."))
            return
        cfg = await self._ensure_config(ctx.guild.id)
        owners = json.loads(cfg.get("owners", "[]"))
        if str(member.id) in owners:
            await ctx.send(embed=antinuke_error("Already Owner", f"<@{member.id}> is already an extra-owner."))
            return
        if len(owners) >= 3:
            await ctx.send(embed=antinuke_error("Limit Reached", "Maximum 3 extra-owners."))
            return
        owners.append(str(member.id))
        await db.execute(
            "UPDATE antinuke_config SET owners = ? WHERE guild_id = ?",
            (json.dumps(owners), ctx.guild.id),
        )
        await ctx.send(embed=antinuke_embed("Extra-Owner Added", f"✅ <@{member.id}> (`{member.id}`) is now an extra-owner.\nTotal: **{len(owners)}/3**"))

    @extraowner_group.command(name="remove")
    @commands.guild_only()
    async def extraowner_remove(self, ctx: commands.Context, member: discord.Member) -> None:
        """Remove an extra-owner."""
        if ctx.author.id != ctx.guild.owner_id and ctx.author.id != config.owner_id:
            await ctx.send(embed=antinuke_error("Permission Denied", "Only the server owner can remove extra-owners."))
            return
        cfg = await self._ensure_config(ctx.guild.id)
        owners = json.loads(cfg.get("owners", "[]"))
        if str(member.id) not in owners:
            await ctx.send(embed=antinuke_error("Not Found", f"<@{member.id}> is not an extra-owner."))
            return
        owners.remove(str(member.id))
        await db.execute(
            "UPDATE antinuke_config SET owners = ? WHERE guild_id = ?",
            (json.dumps(owners), ctx.guild.id),
        )
        await ctx.send(embed=antinuke_embed("Extra-Owner Removed", f"✅ <@{member.id}> removed from extra-owners."))

    @extraowner_group.command(name="show")
    @commands.guild_only()
    async def extraowner_show(self, ctx: commands.Context) -> None:
        """Show all extra-owners."""
        if ctx.author.id != ctx.guild.owner_id and ctx.author.id != config.owner_id:
            await ctx.send(embed=antinuke_error("Permission Denied", "Only the server owner can view extra-owners."))
            return
        cfg = await self._ensure_config(ctx.guild.id)
        owners = json.loads(cfg.get("owners", "[]"))
        if not owners:
            await ctx.send(embed=antinuke_embed("No Extra-Owners", "No extra-owners set."))
            return
        embed = antinuke_embed(f"Extra-Owners — {len(owners)}/3")
        for uid in owners:
            embed.add_field(name=f"`{uid}`", value=f"<@{uid}>", inline=False)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiNukeCog(bot))
