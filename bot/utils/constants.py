"""
Cypher All-In-One Discord Bot - Brand colors & constants.
Matches the original Cypher bot styling exactly.
"""
from __future__ import annotations

import discord


class Colors:
    # ---- Cypher brand colors (exact from original repo) ----
    ANTINUKE = 0xFFFFFF       # White - all AntiNuke command embeds
    ANTINUKE_ERROR = 0xFEE75C # Yellow - AntiNuke permission errors
    AUTOMOD = 0x000000        # Black - all AutoMod command embeds
    AUTOMOD_LOG = 0xff0000    # Red - AutoMod log embeds + punishment embeds
    HELP = 0x2f3136           # Dark grey - help menu, error handler
    SYSTEM = 0x2b2d31         # Slightly lighter grey - system info
    DEFAULT = 0x495063        # Tools default
    GOLD = 0xb4baf7

    # Legacy aliases (for compatibility with existing cogs)
    PRIMARY = 0xFFFFFF
    SECONDARY = 0x2b2d31
    ACCENT = 0xb4baf7
    SUCCESS = 0x57F287
    WARNING = 0xFEE75C
    DANGER = 0xED4245
    ERROR = 0xED4245
    INFO = 0x00B0F4
    SECURITY = 0xFFFFFF
    RAID = 0xff0000
    NUKE = 0xff0000
    AUTO = 0x000000
    LOW = 0x57F287
    MEDIUM = 0xFEE75C
    HIGH = 0xFF8C00
    CRITICAL = 0xED4245
    DARK = 0x1E1E2E
    LIGHT = 0xFFFFFF


class Icons:
    """Custom Cypher emoji placeholders. Replace IDs with your own if needed."""
    # Use unicode fallbacks that work without custom emoji
    SHIELD = "🛡️"
    LOCK = "🔒"
    UNLOCK = "🔓"
    WARNING = "⚠️"
    DANGER = "⛔"
    SUCCESS = "✅"
    ERROR = "❌"
    INFO = "ℹ️"
    BAN = "🔨"
    KICK = "👢"
    TIMEOUT = "⏳"
    RAID = "🚨"
    NUKE = "💥"
    USER = "👤"
    ROLE = "🏷️"
    CHANNEL = "📢"
    BOT = "🤖"
    WHITELIST = "📋"
    SETTINGS = "⚙️"
    STATISTICS = "📊"
    BACKUP = "💾"
    CLOCK = "🕐"
    FIRE = "🔥"
    EYE = "👁️"
    LINK = "🔗"
    SCAM = "🎣"
    VIRUS = "🦠"
    CROWN = "👑"
    STAR = "⭐"
    LANGUAGE = "🌐"
    LIGHTBULB = "💡"
    GEAR = "🔧"
    TRASH = "🗑️"
    PLUS = "➕"
    MINUS = "➖"
    ARROW = "➡"
    CROSS = "❌"
    TICK = "✅"
    DISABLED = "🔴"
    ENABLED = "🟢"
    EXCLAMATION = "❗"


# Reason prefix used by all AntiNuke events
SECURITY_REASON_PREFIX = "Zecurity • Security"

# Bot name (rebranded)
BOT_NAME = "Zecurity"

FOOTER_TEXT = "Zecurity • Built By Arsh"
FOOTER_ICON = "https://i.imgur.com/WNmnU4m.png"
