"""
Embed factory matching Cypher bot styling.
"""
from __future__ import annotations

import discord
from typing import Optional, List
from .constants import Colors, Icons, FOOTER_TEXT, FOOTER_ICON, BOT_NAME


def _base(color: int, title: str, description: Optional[str] = None) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    return embed


# ---- AntiNuke embeds (white by default) ----
def antinuke_embed(title: str, description: Optional[str] = None) -> discord.Embed:
    return _base(Colors.ANTINUKE, title, description)


def antinuke_error(title: str, description: Optional[str] = None) -> discord.Embed:
    return _base(Colors.ANTINUKE_ERROR, f"{Icons.WARNING}  {title}", description)


# ---- AutoMod embeds (black) ----
def automod_embed(title: str, description: Optional[str] = None) -> discord.Embed:
    return _base(Colors.AUTOMOD, title, description)


def automod_log(title: str, description: Optional[str] = None) -> discord.Embed:
    return _base(Colors.AUTOMOD_LOG, title, description)


# ---- Generic aliases ----
def success(title: str, description: Optional[str] = None) -> discord.Embed:
    return _base(Colors.SUCCESS, f"{Icons.SUCCESS}  {title}", description)


def error(title: str, description: Optional[str] = None) -> discord.Embed:
    return _base(Colors.DANGER, f"{Icons.ERROR}  {title}", description)


def warning(title: str, description: Optional[str] = None) -> discord.Embed:
    return _base(Colors.WARNING, f"{Icons.WARNING}  {title}", description)


def info(title: str, description: Optional[str] = None) -> discord.Embed:
    return _base(Colors.INFO, f"{Icons.INFO}  {title}", description)


def security(title: str, description: Optional[str] = None) -> discord.Embed:
    return _base(Colors.SECURITY, f"{Icons.SHIELD}  {title}", description)


def raid_alert(title: str, description: Optional[str] = None) -> discord.Embed:
    return _base(Colors.RAID, f"{Icons.RAID}  {title}", description)


def nuke_alert(title: str, description: Optional[str] = None) -> discord.Embed:
    return _base(Colors.NUKE, f"{Icons.NUKE}  {title}", description)


def severity_embed(severity: str, title: str, description: Optional[str] = None) -> discord.Embed:
    colors = {"low": Colors.LOW, "medium": Colors.MEDIUM, "high": Colors.HIGH, "critical": Colors.CRITICAL}
    color = colors.get(severity.lower(), Colors.INFO)
    return _base(color, title, description)


def user_embed(user, title: str, description: Optional[str] = None, color: int = Colors.PRIMARY, show_thumbnail: bool = True) -> discord.Embed:
    embed = _base(color, title, description)
    if show_thumbnail:
        embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="User", value=f"<@{user.id}>", inline=True)
    embed.add_field(name="User ID", value=f"`{user.id}`", inline=True)
    embed.add_field(name="Account Created", value=f"<t:{int(user.created_at.timestamp())}:R>", inline=True)
    return embed


def punishment_embed(action: str, user, moderator, reason: str, duration: Optional[str] = None) -> discord.Embed:
    embed = _base(Colors.DANGER, f"{Icons.BAN}  {action}", f"**Target:** <@{user.id}> (`{user.id}`)")
    embed.add_field(name="Moderator", value=f"<@{moderator.id}> (`{moderator.id}`)", inline=True)
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=True)
    if duration:
        embed.add_field(name="Duration", value=duration, inline=True)
    embed.set_thumbnail(url=user.display_avatar.url)
    return embed


def raid_embed(raid_type: str, description: str, severity: str = "high", user_count: int = 0) -> discord.Embed:
    embed = raid_alert(f"Raid Detected — {raid_type.title()}", description)
    embed.add_field(name="Type", value=raid_type, inline=True)
    embed.add_field(name="Severity", value=severity.title(), inline=True)
    embed.add_field(name="Users Involved", value=str(user_count), inline=True)
    return embed


def nuke_embed(action_type: str, description: str, actor=None, severity: str = "critical") -> discord.Embed:
    embed = nuke_alert(f"Anti-Nuke Triggered — {action_type}", description)
    embed.add_field(name="Severity", value=severity.title(), inline=True)
    if actor:
        embed.add_field(name="Actor", value=f"<@{actor.id}> (`{actor.id}`)", inline=True)
    return embed


def stats_embed(title: str, stats: dict) -> discord.Embed:
    embed = _base(Colors.INFO, f"{Icons.STATISTICS}  {title}")
    for k, v in stats.items():
        embed.add_field(name=k, value=str(v), inline=True)
    return embed


def help_embed(title: str, commands: List[dict]) -> discord.Embed:
    embed = _base(Colors.HELP, f"{Icons.SHIELD}  {title}")
    for cmd in commands:
        name = cmd.get("name", "")
        desc = cmd.get("description", "")
        usage = cmd.get("usage", "")
        embed.add_field(
            name=f"`{name}`" + (f"  `{usage}`" if usage else ""),
            value=desc,
            inline=False,
        )
    return embed


def owner_alert(title: str, description: str, guild: discord.Guild | None = None) -> discord.Embed:
    embed = _base(Colors.CRITICAL, f"{Icons.CROWN}  {title}", description)
    if guild:
        embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)", inline=True)
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    return embed


def progress_bar(percent: int, length: int = 10) -> str:
    filled = int(length * percent / 100)
    return "█" * filled + "░" * (length - filled)


def score_embed(score: int, factors: dict) -> discord.Embed:
    color = Colors.SUCCESS if score >= 80 else (Colors.WARNING if score >= 50 else Colors.DANGER)
    embed = _base(color, f"{Icons.SHIELD}  Security Score — {score}/100")
    bar = progress_bar(score, 20)
    embed.description = f"```\n{bar} {score}%\n```"
    for factor, val in factors.items():
        embed.add_field(name=factor.replace("_", " ").title(), value=val, inline=False)
    return embed
