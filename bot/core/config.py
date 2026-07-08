"""
Centralized configuration loader.
Reads from environment variables and provides typed accessors.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _get_bool(key: str, default: bool) -> bool:
    val = os.getenv(key, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


@dataclass
class Config:
    # Core
    token: str = os.getenv("DISCORD_TOKEN", "")
    owner_id: int = _get_int("OWNER_ID", 1498693593701945374)
    default_prefix: str = os.getenv("DEFAULT_PREFIX", ".")

    # Database
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/security_bot.db")

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: str = os.getenv("LOG_FILE", "./logs/bot.log")

    # Web API
    web_api_enabled: bool = _get_bool("WEB_API_ENABLED", False)
    web_api_port: int = _get_int("WEB_API_PORT", 8080)
    web_api_token: str = os.getenv("WEB_API_TOKEN", "change_me_in_production")

    # External
    phishing_api_key: str = os.getenv("PHISHING_API_KEY", "")

    # Localization
    default_language: str = os.getenv("DEFAULT_LANGUAGE", "en")

    # Performance
    max_workers: int = _get_int("MAX_WORKERS", 4)
    command_cooldown: int = _get_int("COMMAND_COOLDOWN", 3)

    # Safety
    dry_run: bool = _get_bool("DRY_RUN", False)

    # Railway
    railway_environment: str = os.getenv("RAILWAY_ENVIRONMENT", "production")

    # ---- Default protection thresholds (configurable per-guild via commands) ----
    default_thresholds: dict = field(default_factory=lambda: {
        "antinuke": {
            "channel_delete": 3,        # max channels deletable in 10s window
            "channel_create": 5,
            "role_delete": 3,
            "role_create": 5,
            "webhook_create": 2,
            "emoji_delete": 3,
            "emoji_create": 5,
            "sticker_delete": 2,
            "sticker_create": 3,
            "bot_add": 1,               # zero tolerance for unknown bot adds
            "member_kick": 3,
            "member_ban": 3,
            "member_timeout": 5,
            "member_prune": 1,           # any prune trigger
            "vanity_change": 1,
            "server_update": 1,
            "window_seconds": 10,
            "punishment": "strip",       # strip | ban | kick | quarantine
        },
        "antiraid": {
            "join_threshold": 10,        # joins in window
            "join_window_seconds": 10,
            "leave_threshold": 8,
            "leave_window_seconds": 15,
            "dmraid_threshold": 5,
            "dmraid_window_seconds": 60,
            "new_account_hours": 24,     # accounts younger than this are suspicious
            "auto_lockdown_on_raid": True,
            "quarantine_role_name": "Quarantined",
            "punishment": "kick",        # kick | ban | quarantine
        },
        "antispam": {
            "message_threshold": 7,      # messages in window
            "window_seconds": 5,
            "mention_threshold": 5,
            "duplicate_threshold": 3,
            "caps_percentage": 70,
            "caps_min_length": 8,
            "emoji_max_per_message": 15,
            "attachment_threshold": 5,
            "punishment": "timeout",     # timeout | kick | ban
            "timeout_seconds": 600,
        },
        "automod": {
            "bad_words_enabled": True,
            "invite_links_enabled": True,
            "scam_links_enabled": True,
            "phishing_enabled": True,
            "ip_loggers_enabled": True,
            "nsfw_enabled": True,
            "flood_message_threshold": 10,
            "flood_window_seconds": 5,
            "punishment": "delete",      # delete | warn | timeout | kick | ban
        },
        "antitamper": {
            "protected_roles": [],       # role IDs that can't be modified by non-owners
            "block_self_bot_promotion": True,
            "block_token_sharing": True,
        },
    })

    def validate(self) -> list[str]:
        """Return list of validation errors (empty if OK)."""
        errors = []
        if not self.token:
            errors.append("DISCORD_TOKEN is required")
        if self.owner_id == 0:
            errors.append("OWNER_ID is required")
        return errors


config = Config()
