"""
Feature flag system - allows admins to enable/disable each protection module
per guild. All features are enabled by default.

Features:
- antinuke
- antiraid
- automod
- antispam
- antitamper
- verification
- logging

Stored in guild_settings.settings.features as a JSON dict.
"""
from __future__ import annotations

from .database import db
from .logger import setup_logger

log = setup_logger("securitybot.features")

ALL_FEATURES = {
    "antinuke": "Anti-Nuke protection (Cypher-style: 11 modules, Ban/Kick/Strip, auto-recovery)",
    "antiraid": "Anti-Raid detection (join/leave/bot raids, auto-lockdown, quarantine)",
    "automod": "AutoMod message filtering (Cypher-style: 6 modules - spam/caps/link/invites/mention/emoji)",
    "antispam": "Legacy AntiSpam rate-limiting (separate from AutoMod Anti-spam)",
    "antitamper": "AntiTamper protection (token sharing, dangerous permission grants)",
    "verification": "Verification system (new member gate)",
    "logging": "Audit and event logging to log channels",
    "backup": "Server backup & restore",
}


async def is_feature_enabled(guild_id: int, feature: str) -> bool:
    """
    Check if a feature is enabled for a guild.
    Returns True by default for all features.
    """
    if feature not in ALL_FEATURES:
        return True  # unknown features default to enabled
    settings = await db.get_guild_settings(guild_id)
    features = settings.get("features", {})
    # Default: enabled
    return features.get(feature, True)


async def set_feature_flag(guild_id: int, feature: str, enabled: bool) -> None:
    """Enable or disable a feature for a guild."""
    if feature not in ALL_FEATURES:
        raise ValueError(f"Unknown feature: {feature}")
    settings = await db.get_guild_settings(guild_id)
    features = settings.get("features", {})
    features[feature] = enabled
    settings["features"] = features
    await db.set_guild_settings(guild_id, settings)
    log.info("Feature %s set to %s for guild %s", feature, enabled, guild_id)


async def get_all_feature_flags(guild_id: int) -> dict[str, bool]:
    """Return dict of all features and their enabled state for a guild."""
    settings = await db.get_guild_settings(guild_id)
    features = settings.get("features", {})
    return {name: features.get(name, True) for name in ALL_FEATURES}


async def toggle_feature(guild_id: int, feature: str) -> bool:
    """Toggle a feature. Returns the new state."""
    current = await is_feature_enabled(guild_id, feature)
    new_state = not current
    await set_feature_flag(guild_id, feature, new_state)
    return new_state
