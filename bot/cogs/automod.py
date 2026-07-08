"""
AutoMod cog - Cypher-style exact replica.

6 modules (matching Cypher thresholds exactly):
- AntiSpam: >5 messages in 10s sliding window -> 10min timeout
- AntiCaps: >70% uppercase AND >=45 chars -> 2min timeout, delete msg
- AntiLink: http(s):// regex, exempt Discord invites/GIFs/Spotify -> 5min timeout, delete msg
- AntiInvites: discord.gg/discordapp.com/invite/discord.com/invite regex (exempt own invites) -> 10min timeout, delete msg
- AntiMassMention: >=5 mentions (<@) -> 2min timeout, delete msg
- AntiEmojiSpam: >5 emojis -> 2min timeout, delete msg

Punishments: Mute (timeout) / Kick / Ban (default Mute)
Color: 0x000000 (commands), 0xff0000 (logs + punishment embeds)
"""
from __future__ import annotations

import re
import time
import discord
from collections import deque, defaultdict
from discord.ext import commands
from typing import Optional

from ..core.config import config
from ..core.database import db
from ..core.permissions import is_whitelisted
from ..core.logger import setup_logger
from ..utils.embeds import automod_embed, automod_log, info, error, success, warning
from ..utils.constants import Colors, Icons
from ..utils.helpers import safe_send

log = setup_logger("securitybot.automod")

# Cypher-style thresholds (exact)
SPAM_THRESHOLD = 5          # >5 messages
SPAM_WINDOW_SECONDS = 10    # in 10s
CAPS_THRESHOLD = 70         # >70% uppercase
CAPS_MIN_LENGTH = 45        # AND >=45 chars
MASS_MENTION_THRESHOLD = 5  # >=5 mentions
EMOJI_THRESHOLD = 5         # >5 emojis

# Punishment durations (when Mute=timeout)
PUNISH_DURATIONS = {
    "Anti spam": 600,     # 10 min
    "Anti caps": 120,     # 2 min
    "Anti link": 300,     # 5 min
    "Anti invites": 600,  # 10 min
    "Anti mass mention": 120,  # 2 min
    "Anti emoji spam": 120,    # 2 min
}

# Regex patterns (matching Cypher exactly)
URL_RE = re.compile(r"http[s]?://\S+", re.IGNORECASE)
INVITE_RE = re.compile(
    r"(discord\.gg|discordapp\.com/invite|discord\.com/invite)/([a-zA-Z0-9-]+)",
    re.IGNORECASE,
)

# Exemption substrings for AntiLink (matching Cypher)
LINK_EXEMPTIONS = [
    "discord.gg/", "discordapp.com/invite", "discord.com/invite",
    "tenor.com", "giphy.com",
    "cdn.discordapp.com", "media.discordapp.com",
    "open.spotify.com/track", "spotify.com/track",
]

# Unicode emoji ranges
EMOJI_RE = re.compile(
    "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
    "\U00002600-\U000027BF\U0001F1E0-\U0001F1FF]"
)
CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")


def count_emojis(text: str) -> int:
    return len(EMOJI_RE.findall(text)) + len(CUSTOM_EMOJI_RE.findall(text))


def is_caps_spam(text: str) -> bool:
    if len(text) < CAPS_MIN_LENGTH:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    upper = sum(1 for c in letters if c.isupper())
    return (upper / len(letters) * 100) > CAPS_THRESHOLD


class AutoModCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # {(guild_id, user_id, channel_id): deque[ts]} for spam tracking
        self._spam_tracker: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=20))

    # ---------- DB helpers ----------
    async def _is_enabled(self, guild_id: int) -> bool:
        row = await db.fetchone("SELECT enabled FROM automod WHERE guild_id = ?", (guild_id,))
        return bool(row and row["enabled"])

    async def _is_event_enabled(self, guild_id: int, event: str) -> bool:
        row = await db.fetchone(
            "SELECT punishment FROM automod_punishments WHERE guild_id = ? AND event = ?",
            (guild_id, event),
        )
        return row is not None

    async def _get_punishment(self, guild_id: int, event: str) -> str:
        row = await db.fetchone(
            "SELECT punishment FROM automod_punishments WHERE guild_id = ? AND event = ?",
            (guild_id, event),
        )
        return row["punishment"] if row else "Mute"

    async def _is_ignored(self, guild_id: int, channel_id: int, member: discord.Member) -> bool:
        # Check ignored channels
        ch_row = await db.fetchone(
            "SELECT 1 FROM automod_ignored WHERE guild_id = ? AND type = 'channel' AND entity_id = ?",
            (guild_id, channel_id),
        )
        if ch_row:
            return True
        # Check ignored roles
        for role in member.roles:
            r_row = await db.fetchone(
                "SELECT 1 FROM automod_ignored WHERE guild_id = ? AND type = 'role' AND entity_id = ?",
                (guild_id, role.id),
            )
            if r_row:
                return True
        return False

    async def _punish(self, message: discord.Message, event: str, reason: str) -> None:
        """Apply the configured punishment."""
        punishment = await self._get_punishment(message.guild.id, event)
        member = message.author
        try:
            if punishment == "Ban":
                await member.ban(reason=f"Zecurity • Automod | {event}: {reason}", delete_message_days=1)
            elif punishment == "Kick":
                await member.kick(reason=f"Zecurity • Automod | {event}: {reason}")
            else:  # Mute = timeout
                duration = PUNISH_DURATIONS.get(event, 300)
                until = discord.utils.utcnow() + discord.utils.timedelta(seconds=duration)
                await member.timeout(until, reason=f"Zecurity • Automod | {event}: {reason}")

                # Send user-facing punishment embed (red, matching Cypher)
                punish_embed = discord.Embed(color=0xff0000)
                punish_embed.description = (
                    f"⚠️ You've been timed out by **Zecurity Automod** for {event}\n"
                    f"**Reason:** {reason}."
                )
                await safe_send(message.channel, content=member.mention, embed=punish_embed, delete_after=30)

        except discord.Forbidden:
            log.warning("Cannot punish %s in %s - missing permissions", member.id, message.guild.id)
        except Exception as e:
            log.exception("Automod punish failed: %s", e)

        # Log to channel
        await self._log_violation(message, event, reason, punishment)

        # Record user history
        await db.execute(
            """INSERT INTO user_history (guild_id, user_id, action, moderator_id, reason)
               VALUES (?, ?, 'automod_violation', ?, ?)""",
            (message.guild.id, member.id, self.bot.user.id, f"Automod: {event} - {reason} ({punishment})"),
        )

    async def _log_violation(self, message: discord.Message, event: str, reason: str, punishment: str) -> None:
        # AutoMod log channel
        row = await db.fetchone("SELECT log_channel FROM automod WHERE guild_id = ?", (message.guild.id,))
        if row and row["log_channel"]:
            channel = message.guild.get_channel(row["log_channel"])
            if channel:
                embed = discord.Embed(
                    title=f"Automod Log: {event}",
                    color=0xff0000,
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="User", value=f"<@{message.author.id}> (`{message.author.id}`)", inline=True)
                embed.add_field(name="Action", value=punishment, inline=True)
                embed.add_field(name="Channel", value=f"<#{message.channel.id}>", inline=True)
                embed.add_field(name="Reason", value=reason, inline=False)
                embed.set_thumbnail(url=message.author.display_avatar.url)
                embed.set_footer(text=f"User ID: {message.author.id}")
                await safe_send(channel, embed=embed)

        # Also send to security_log if configured
        sec_row = await db.fetchone("SELECT security_log FROM log_channels WHERE guild_id = ?", (message.guild.id,))
        if sec_row and sec_row["security_log"]:
            channel = message.guild.get_channel(sec_row["security_log"])
            if channel:
                embed = discord.Embed(
                    title=f"Automod Log: {event}",
                    color=0xff0000,
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="User", value=f"<@{message.author.id}> (`{message.author.id}`)", inline=True)
                embed.add_field(name="Action", value=punishment, inline=True)
                embed.add_field(name="Channel", value=f"<#{message.channel.id}>", inline=True)
                embed.add_field(name="Reason", value=reason, inline=False)
                embed.set_thumbnail(url=message.author.display_avatar.url)
                embed.set_footer(text=f"User ID: {message.author.id}")
                await safe_send(channel, embed=embed)

    # ---------- Main message handler ----------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        if not await self._is_enabled(message.guild.id):
            return
        if await is_whitelisted(message.author):
            return
        if message.author.guild_permissions.administrator:
            return
        if message.author.id == message.guild.owner_id:
            return
        if await self._is_ignored(message.guild.id, message.channel.id, message.author):
            return

        content = message.content
        if not content:
            return

        triggered = False
        event = ""
        reason = ""

        # ---- 1. AntiSpam: >5 messages in 10s sliding window ----
        if await self._is_event_enabled(message.guild.id, "Anti spam"):
            key = (message.guild.id, message.author.id, message.channel.id)
            now = time.time()
            self._spam_tracker[key].append(now)
            # Prune old
            cutoff = now - SPAM_WINDOW_SECONDS
            while self._spam_tracker[key] and self._spam_tracker[key][0] < cutoff:
                self._spam_tracker[key].popleft()
            if len(self._spam_tracker[key]) > SPAM_THRESHOLD:
                triggered = True
                event = "Anti spam"
                reason = f"Sent {len(self._spam_tracker[key])} messages in {SPAM_WINDOW_SECONDS}s"

        # ---- 2. AntiCaps: >70% uppercase AND >=45 chars ----
        if not triggered and await self._is_event_enabled(message.guild.id, "Anti caps"):
            if is_caps_spam(content):
                triggered = True
                event = "Anti caps"
                reason = f"Message is >{CAPS_THRESHOLD}% uppercase"
                try:
                    await message.delete()
                except (discord.Forbidden, discord.HTTPException):
                    pass

        # ---- 3. AntiLink ----
        if not triggered and await self._is_event_enabled(message.guild.id, "Anti link"):
            urls = URL_RE.findall(content)
            if urls:
                # Check exemptions
                exempt = False
                for url in urls:
                    url_lower = url.lower()
                    if any(ex in url_lower for ex in LINK_EXEMPTIONS):
                        exempt = True
                        break
                if not exempt:
                    triggered = True
                    event = "Anti link"
                    reason = f"Posted link: {urls[0]}"
                    try:
                        await message.delete()
                    except (discord.Forbidden, discord.HTTPException):
                        pass

        # ---- 4. AntiInvites ----
        if not triggered and await self._is_event_enabled(message.guild.id, "Anti invites"):
            invites = INVITE_RE.findall(content)
            if invites:
                # Check if it's the guild's own invite
                own_invites = []
                try:
                    own_invites = [i.code for i in await message.guild.invites()]
                except discord.Forbidden:
                    pass
                for _, code in invites:
                    if code not in own_invites:
                        triggered = True
                        event = "Anti invites"
                        reason = f"Posted invite: discord.gg/{code}"
                        try:
                            await message.delete()
                        except (discord.Forbidden, discord.HTTPException):
                            pass
                        break

        # ---- 5. AntiMassMention: >=5 mentions ----
        if not triggered and await self._is_event_enabled(message.guild.id, "Anti mass mention"):
            mention_count = content.count("<@")
            if mention_count >= MASS_MENTION_THRESHOLD:
                triggered = True
                event = "Anti mass mention"
                reason = f"Mass mentioned {mention_count} users"
                try:
                    await message.delete()
                except (discord.Forbidden, discord.HTTPException):
                    pass

        # ---- 6. AntiEmojiSpam: >5 emojis ----
        if not triggered and await self._is_event_enabled(message.guild.id, "Anti emoji spam"):
            emoji_count = count_emojis(content)
            if emoji_count > EMOJI_THRESHOLD:
                triggered = True
                event = "Anti emoji spam"
                reason = f"Posted {emoji_count} emojis"
                try:
                    await message.delete()
                except (discord.Forbidden, discord.HTTPException):
                    pass

        if not triggered:
            return

        # Apply punishment
        await self._punish(message, event, reason)

    # ---------- Commands (Cypher-style) ----------

    @commands.hybrid_group(name="automod", invoke_without_command=True)
    @commands.guild_only()
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def automod_group(self, ctx: commands.Context) -> None:
        """AutoMod message filtering system."""
        if ctx.subcommand_passed is None:
            embed = automod_embed(
                "AutoMod Commands",
                f"```\n{ctx.prefix}automod enable                  - Enable AutoMod\n"
                f"{ctx.prefix}automod disable                 - Disable AutoMod\n"
                f"{ctx.prefix}automod punishment <event> <Mute|Kick|Ban>  - Set punishment\n"
                f"{ctx.prefix}automod config                  - View configuration\n"
                f"{ctx.prefix}automod logging <#channel>      - Set log channel\n"
                f"{ctx.prefix}automod ignore channel <#chan>  - Ignore a channel\n"
                f"{ctx.prefix}automod ignore role <@role>     - Ignore a role\n"
                f"{ctx.prefix}automod ignore show             - Show ignored\n"
                f"{ctx.prefix}automod ignore reset            - Reset ignored\n```",
            )
            await ctx.send(embed=embed)

    @automod_group.command(name="enable")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_guild=True)
    async def automod_enable(self, ctx: commands.Context, *, events: str = None) -> None:
        """Enable AutoMod. Optionally specify events to enable (or 'all')."""
        # Role hierarchy check
        if ctx.author.top_role.position < ctx.guild.me.top_role.position:
            await ctx.send(embed=error("Permission Denied", "Your top role must be above the bot's top role."))
            return

        await db._ensure_guild_exists(ctx.guild.id)
        # Ensure automod row exists
        existing = await db.fetchone("SELECT 1 FROM automod WHERE guild_id = ?", (ctx.guild.id,))
        if not existing:
            await db.execute("INSERT INTO automod (guild_id, enabled) VALUES (?, 1)", (ctx.guild.id,))
        else:
            await db.execute("UPDATE automod SET enabled = 1 WHERE guild_id = ?", (ctx.guild.id,))

        # Enable all events by default
        all_events = ["Anti spam", "Anti caps", "Anti link", "Anti invites", "Anti mass mention", "Anti emoji spam"]
        for ev in all_events:
            await db.execute(
                "INSERT OR IGNORE INTO automod_punishments (guild_id, event, punishment) VALUES (?, ?, 'Mute')",
                (ctx.guild.id, ev),
            )

        embed = automod_embed(
            "AutoMod Enabled",
            f"✅ AutoMod is now **enabled** for **{ctx.guild.name}**.\n"
            f"All 6 modules active with default punishment **Mute**.\n\n"
            f"**Active Modules:**\n"
            f"• Anti Spam (>5 msgs/10s -> 10m timeout)\n"
            f"• Anti Caps (>70% caps, ≥45 chars -> 2m timeout)\n"
            f"• Anti Link (with Discord/GIF/Spotify exemptions -> 5m timeout)\n"
            f"• Anti Invites (discord.gg links -> 10m timeout)\n"
            f"• Anti Mass Mention (≥5 mentions -> 2m timeout)\n"
            f"• Anti Emoji Spam (>5 emojis -> 2m timeout)",
        )
        await ctx.send(embed=embed)

    @automod_group.command(name="disable")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def automod_disable(self, ctx: commands.Context) -> None:
        """Disable AutoMod."""
        if ctx.author.top_role.position < ctx.guild.me.top_role.position:
            await ctx.send(embed=error("Permission Denied", "Your top role must be above the bot's top role."))
            return
        await db._ensure_guild_exists(ctx.guild.id)
        existing = await db.fetchone("SELECT 1 FROM automod WHERE guild_id = ?", (ctx.guild.id,))
        if not existing:
            await db.execute("INSERT INTO automod (guild_id, enabled) VALUES (?, 0)", (ctx.guild.id,))
        else:
            await db.execute("UPDATE automod SET enabled = 0 WHERE guild_id = ?", (ctx.guild.id,))
        await ctx.send(embed=automod_embed("AutoMod Disabled", f"⚠️ AutoMod is now **disabled** for **{ctx.guild.name}**."))

    @automod_group.command(name="punishment", aliases=["punish"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def automod_punishment(self, ctx: commands.Context, event: str = None, punishment: str = None) -> None:
        """Set punishment for an event. Example: .automod punishment 'Anti spam' Ban"""
        if ctx.author.top_role.position < ctx.guild.me.top_role.position:
            await ctx.send(embed=error("Permission Denied", "Your top role must be above the bot's top role."))
            return
        valid_events = ["Anti spam", "Anti caps", "Anti link", "Anti invites", "Anti mass mention", "Anti emoji spam"]
        valid_punishments = ["Mute", "Kick", "Ban"]

        if event is None or punishment is None:
            embed = automod_embed(
                "AutoMod Punishment",
                f"Usage: `{ctx.prefix}automod punishment <event> <punishment>`\n\n"
                f"**Events:** {', '.join(f'`{e}`' for e in valid_events)}\n"
                f"**Punishments:** {', '.join(f'`{p}`' for p in valid_punishments)}\n"
                f"Default: `Mute` (timeout)",
            )
            await ctx.send(embed=embed)
            return

        # Match event (case-insensitive)
        matched_event = next((e for e in valid_events if e.lower() == event.lower()), None)
        if not matched_event:
            await ctx.send(embed=error("Invalid Event", f"Valid events: {', '.join(valid_events)}"))
            return

        matched_punishment = next((p for p in valid_punishments if p.lower() == punishment.lower()), None)
        if not matched_punishment:
            await ctx.send(embed=error("Invalid Punishment", f"Valid: {', '.join(valid_punishments)}"))
            return

        await db._ensure_guild_exists(ctx.guild.id)
        await db.execute(
            """INSERT OR REPLACE INTO automod_punishments (guild_id, event, punishment) VALUES (?, ?, ?)""",
            (ctx.guild.id, matched_event, matched_punishment),
        )
        await ctx.send(embed=automod_embed("Punishment Updated", f"✅ `{matched_event}` punishment set to **{matched_punishment}**"))

    @automod_group.command(name="config", aliases=["settings", "show", "view"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def automod_config(self, ctx: commands.Context) -> None:
        """View AutoMod configuration."""
        row = await db.fetchone("SELECT * FROM automod WHERE guild_id = ?", (ctx.guild.id,))
        enabled = bool(row and row["enabled"])
        log_channel = row["log_channel"] if row else None

        punishments = await db.fetchall(
            "SELECT event, punishment FROM automod_punishments WHERE guild_id = ?",
            (ctx.guild.id,),
        )
        pun_map = {p["event"]: p["punishment"] for p in punishments}

        ignored_channels = await db.fetchall(
            "SELECT entity_id FROM automod_ignored WHERE guild_id = ? AND type = 'channel'",
            (ctx.guild.id,),
        )
        ignored_roles = await db.fetchall(
            "SELECT entity_id FROM automod_ignored WHERE guild_id = ? AND type = 'role'",
            (ctx.guild.id,),
        )

        embed = automod_embed(f"AutoMod Configuration — {ctx.guild.name}")
        embed.add_field(name="Status", value="🟢 Enabled" if enabled else "🔴 Disabled", inline=True)
        embed.add_field(name="Log Channel", value=f"<#{log_channel}>" if log_channel else "None", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        modules_str = ""
        for ev in ["Anti spam", "Anti caps", "Anti link", "Anti invites", "Anti mass mention", "Anti emoji spam"]:
            status = "🟢" if ev in pun_map else "🔴"
            pun = pun_map.get(ev, "Mute")
            modules_str += f"{status} `{ev}` → **{pun}**\n"
        embed.add_field(name="Modules", value=modules_str, inline=False)

        ch_list = ", ".join(f"<#{c['entity_id']}>" for c in ignored_channels) or "None"
        r_list = ", ".join(f"<@&{r['entity_id']}>" for r in ignored_roles) or "None"
        embed.add_field(name="Ignored Channels", value=ch_list, inline=True)
        embed.add_field(name="Ignored Roles", value=r_list, inline=True)
        await ctx.send(embed=embed)

    @automod_group.command(name="logging")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def automod_logging(self, ctx: commands.Context, channel: discord.TextChannel = None) -> None:
        """Set the AutoMod log channel."""
        if ctx.author.top_role.position < ctx.guild.me.top_role.position:
            await ctx.send(embed=error("Permission Denied", "Your top role must be above the bot's top role."))
            return
        await db._ensure_guild_exists(ctx.guild.id)
        existing = await db.fetchone("SELECT 1 FROM automod WHERE guild_id = ?", (ctx.guild.id,))
        channel_id = channel.id if channel else None
        if not existing:
            await db.execute("INSERT INTO automod (guild_id, enabled, log_channel) VALUES (?, 0, ?)", (ctx.guild.id, channel_id))
        else:
            await db.execute("UPDATE automod SET log_channel = ? WHERE guild_id = ?", (channel_id, ctx.guild.id))
        if channel:
            await ctx.send(embed=automod_embed("Log Channel Set", f"✅ AutoMod logs will be sent to <#{channel.id}>"))
        else:
            await ctx.send(embed=automod_embed("Log Channel Cleared", "AutoMod logging disabled."))

    # ---- Ignore subcommands ----

    @automod_group.group(name="ignore", aliases=["exempt", "whitelist", "wl"], invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def automod_ignore(self, ctx: commands.Context) -> None:
        """Manage AutoMod ignored channels/roles."""
        if ctx.subcommand_passed is None:
            embed = automod_embed(
                "AutoMod Ignore Commands",
                f"```\n{ctx.prefix}automod ignore channel <#channel>\n"
                f"{ctx.prefix}automod ignore role <@role>\n"
                f"{ctx.prefix}automod ignore show\n"
                f"{ctx.prefix}automod ignore reset\n```",
            )
            await ctx.send(embed=embed)

    @automod_ignore.command(name="channel")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def ignore_channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await db._ensure_guild_exists(ctx.guild.id)
        try:
            await db.execute(
                "INSERT OR IGNORE INTO automod_ignored (guild_id, type, entity_id) VALUES (?, 'channel', ?)",
                (ctx.guild.id, channel.id),
            )
            await ctx.send(embed=automod_embed("Channel Ignored", f"✅ <#{channel.id}> is now ignored by AutoMod."))
        except Exception:
            await ctx.send(embed=error("Error", "Could not ignore channel."))

    @automod_ignore.command(name="role")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def ignore_role(self, ctx: commands.Context, role: discord.Role) -> None:
        await db._ensure_guild_exists(ctx.guild.id)
        try:
            await db.execute(
                "INSERT OR IGNORE INTO automod_ignored (guild_id, type, entity_id) VALUES (?, 'role', ?)",
                (ctx.guild.id, role.id),
            )
            await ctx.send(embed=automod_embed("Role Ignored", f"✅ <@&{role.id}> is now ignored by AutoMod."))
        except Exception:
            await ctx.send(embed=error("Error", "Could not ignore role."))

    @automod_ignore.command(name="show", aliases=["view", "list", "config"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def ignore_show(self, ctx: commands.Context) -> None:
        channels = await db.fetchall(
            "SELECT entity_id FROM automod_ignored WHERE guild_id = ? AND type = 'channel'",
            (ctx.guild.id,),
        )
        roles = await db.fetchall(
            "SELECT entity_id FROM automod_ignored WHERE guild_id = ? AND type = 'role'",
            (ctx.guild.id,),
        )
        embed = automod_embed("AutoMod Ignored")
        ch_list = "\n".join(f"<#{c['entity_id']}>" for c in channels) or "None"
        r_list = "\n".join(f"<@&{r['entity_id']}>" for r in roles) or "None"
        embed.add_field(name="Channels", value=ch_list, inline=False)
        embed.add_field(name="Roles", value=r_list, inline=False)
        await ctx.send(embed=embed)

    @automod_ignore.command(name="reset")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def ignore_reset(self, ctx: commands.Context) -> None:
        await db.execute("DELETE FROM automod_ignored WHERE guild_id = ?", (ctx.guild.id,))
        await ctx.send(embed=automod_embed("Reset", "✅ All ignored channels and roles cleared."))

    # ---- Unignore (alias for ignore remove) ----

    @automod_group.group(name="unignore", aliases=["unwhitelist", "unwl"], invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def automod_unignore(self, ctx: commands.Context) -> None:
        if ctx.subcommand_passed is None:
            await ctx.send(embed=automod_embed("Unignore", f"Use `{ctx.prefix}automod unignore channel <#chan>` or `{ctx.prefix}automod unignore role <@role>`"))

    @automod_unignore.command(name="channel")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def unignore_channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        cur = await db.execute(
            "DELETE FROM automod_ignored WHERE guild_id = ? AND type = 'channel' AND entity_id = ?",
            (ctx.guild.id, channel.id),
        )
        if cur.rowcount:
            await ctx.send(embed=automod_embed("Removed", f"✅ <#{channel.id}> is no longer ignored."))
        else:
            await ctx.send(embed=error("Not Found", "That channel was not ignored."))

    @automod_unignore.command(name="role")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def unignore_role(self, ctx: commands.Context, role: discord.Role) -> None:
        cur = await db.execute(
            "DELETE FROM automod_ignored WHERE guild_id = ? AND type = 'role' AND entity_id = ?",
            (ctx.guild.id, role.id),
        )
        if cur.rowcount:
            await ctx.send(embed=automod_embed("Removed", f"✅ <@&{role.id}> is no longer ignored."))
        else:
            await ctx.send(embed=error("Not Found", "That role was not ignored."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoModCog(bot))
