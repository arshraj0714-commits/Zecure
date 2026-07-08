"""
General-purpose helpers used across cogs.
"""
from __future__ import annotations

import discord
import re
import time
import asyncio
from typing import Optional, Iterable
from .constants import Icons


# ---- Time helpers ----
def parse_duration(s: str) -> Optional[int]:
    """Parse '30s', '10m', '2h', '7d' into seconds. Returns None on invalid."""
    if not s:
        return None
    s = s.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    if s[-1] not in units:
        try:
            return int(s)  # plain seconds
        except ValueError:
            return None
    try:
        return int(s[:-1]) * units[s[-1]]
    except (ValueError, IndexError):
        return None


def format_duration(seconds: int) -> str:
    """Format seconds into a human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{h}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def humanize_number(n: int) -> str:
    """1000 -> 1k, 1500 -> 1.5k, 1000000 -> 1M."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n/1000:.1f}k".rstrip("0.").rstrip(".")
    return f"{n/1_000_000:.1f}M".rstrip("0.").rstrip(".")


def chunks(lst: list, n: int):
    """Yield n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


# ---- Discord permission helpers ----
def get_top_role_position(member: discord.Member) -> int:
    return member.top_role.position if member.top_role else 0


def can_act_on(bot: discord.Client, guild: discord.Guild, moderator: discord.Member, target: discord.Member) -> bool:
    """Returns True if moderator can act on target."""
    if target.id == guild.owner_id:
        return False
    if target.id == bot.user.id:
        return False
    if moderator.id == guild.owner_id:
        return True
    return get_top_role_position(moderator) > get_top_role_position(target)


# ---- String / pattern helpers ----
DISCORD_INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/([a-zA-Z0-9-]+)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
MENTION_RE = re.compile(r"<@!?\d+>|<#\d+>|<@&\d+>")


def extract_invites(text: str) -> list:
    return DISCORD_INVITE_RE.findall(text)


def extract_urls(text: str) -> list:
    return URL_RE.findall(text)


def count_mentions(text: str) -> int:
    return len(MENTION_RE.findall(text))


def count_emojis(text: str) -> int:
    # Unicode emoji + custom Discord emojis
    custom = len(re.findall(r"<a?:\w+:\d+>", text))
    # crude unicode emoji count
    unicode_emoji = len(re.findall(
        "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
        "\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF"
        "\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
        "\U00002600-\U000027BF\U0001F1E0-\U0001F1FF]", text
    ))
    return custom + unicode_emoji


def is_caps_spam(text: str, threshold_pct: int = 70, min_length: int = 8) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < min_length:
        return False
    upper = sum(1 for c in letters if c.isupper())
    return (upper / len(letters) * 100) >= threshold_pct


def normalize_text(text: str) -> str:
    """Lowercase, strip diacritics-equivalent (basic), remove zero-width chars."""
    text = text.lower()
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---- Async helpers ----
async def safe_send(channel: discord.abc.Messageable, *args, **kwargs) -> Optional[discord.Message]:
    """Send a message, swallowing permission errors."""
    try:
        return await channel.send(*args, **kwargs)
    except (discord.Forbidden, discord.HTTPException):
        return None


async def safe_dm(user: discord.User | discord.Member, *args, **kwargs) -> Optional[discord.Message]:
    """Send a DM, swallowing errors (user may have DMs closed)."""
    try:
        return await user.send(*args, **kwargs)
    except (discord.Forbidden, discord.HTTPException):
        return None


async def notify_owner(bot: discord.Client, embed: discord.Embed) -> None:
    """Send an alert DM to the configured owner."""
    from ..core.config import config
    try:
        owner = bot.get_user(config.owner_id) or await bot.fetch_user(config.owner_id)
        if owner:
            await owner.send(embed=embed)
    except Exception:
        pass


async def quarantine_member(member: discord.Member, reason: str = "Quarantined by SecurityBot") -> bool:
    """Strip all roles and apply quarantine role. Returns True on success."""
    from ..core.database import db
    # Get or create quarantine role
    quar_role = discord.utils.get(member.guild.roles, name="Quarantined")
    if not quar_role:
        try:
            quar_role = await member.guild.create_role(
                name="Quarantined",
                permissions=discord.Permissions.none(),
                reason="SecurityBot: quarantine role creation",
            )
            # Deny send permissions in all channels
            for channel in member.guild.text_channels:
                await channel.set_permissions(
                    quar_role, send_messages=False, view_channel=False, reason="SecurityBot quarantine"
                )
        except discord.Forbidden:
            return False

    # Save old roles
    import json
    role_ids = [r.id for r in member.roles if r != member.guild.default_role]
    await db.execute(
        """INSERT OR REPLACE INTO quarantined_users (guild_id, user_id, reason, quarantined_by, roles)
           VALUES (?, ?, ?, ?, ?)""",
        (member.guild.id, member.id, reason, member.guild.me.id, json.dumps(role_ids)),
    )

    try:
        await member.edit(roles=[quar_role], reason=f"SecurityBot: {reason}")
        return True
    except discord.Forbidden:
        return False


async def unquarantine_member(member: discord.Member) -> bool:
    """Restore roles from quarantine."""
    from ..core.database import db
    import json
    row = await db.fetchone(
        "SELECT roles FROM quarantined_users WHERE guild_id = ? AND user_id = ?",
        (member.guild.id, member.id),
    )
    if not row:
        return False
    role_ids = json.loads(row["roles"]) if row["roles"] else []
    roles = [member.guild.get_role(rid) for rid in role_ids]
    roles = [r for r in roles if r is not None]
    try:
        await member.edit(roles=roles, reason="SecurityBot: released from quarantine")
        await db.execute(
            "DELETE FROM quarantined_users WHERE guild_id = ? AND user_id = ?",
            (member.guild.id, member.id),
        )
        return True
    except discord.Forbidden:
        return False
