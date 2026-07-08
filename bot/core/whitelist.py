"""
Whitelist management for users, roles, and channels.
Whitelisted entities bypass security checks (AntiNuke, AutoMod, AntiSpam, etc.)
"""
from __future__ import annotations

from typing import Optional
from .database import db
from .logger import setup_logger

log = setup_logger(__name__)


async def add_to_whitelist(guild_id: int, entity_id: int, entity_type: str, added_by: int, reason: str = "") -> bool:
    """Add user/role/channel to whitelist. Returns True if added, False if already present."""
    existing = await db.fetchone(
        "SELECT 1 FROM whitelist WHERE guild_id = ? AND entity_id = ? AND entity_type = ?",
        (guild_id, entity_id, entity_type),
    )
    if existing:
        return False
    await db.execute(
        "INSERT INTO whitelist (guild_id, entity_id, entity_type, added_by, reason) VALUES (?, ?, ?, ?, ?)",
        (guild_id, entity_id, entity_type, added_by, reason or "No reason provided"),
    )
    log.info("Whitelist add: guild=%s entity=%s type=%s by=%s", guild_id, entity_id, entity_type, added_by)
    return True


async def remove_from_whitelist(guild_id: int, entity_id: int, entity_type: str) -> bool:
    """Remove entity from whitelist."""
    cur = await db.execute(
        "DELETE FROM whitelist WHERE guild_id = ? AND entity_id = ? AND entity_type = ?",
        (guild_id, entity_id, entity_type),
    )
    return cur.rowcount > 0


async def get_whitelist(guild_id: int, entity_type: Optional[str] = None) -> list:
    if entity_type:
        return await db.fetchall(
            "SELECT * FROM whitelist WHERE guild_id = ? AND entity_type = ? ORDER BY added_at DESC",
            (guild_id, entity_type),
        )
    return await db.fetchall(
        "SELECT * FROM whitelist WHERE guild_id = ? ORDER BY added_at DESC",
        (guild_id,),
    )


async def is_whitelisted_user(guild_id: int, user_id: int) -> bool:
    row = await db.fetchone(
        "SELECT 1 FROM whitelist WHERE guild_id = ? AND entity_id = ? AND entity_type = 'user'",
        (guild_id, user_id),
    )
    return row is not None


async def is_whitelisted_role(guild_id: int, role_id: int) -> bool:
    row = await db.fetchone(
        "SELECT 1 FROM whitelist WHERE guild_id = ? AND entity_id = ? AND entity_type = 'role'",
        (guild_id, role_id),
    )
    return row is not None


async def is_whitelisted_channel(guild_id: int, channel_id: int) -> bool:
    row = await db.fetchone(
        "SELECT 1 FROM whitelist WHERE guild_id = ? AND entity_id = ? AND entity_type = 'channel'",
        (guild_id, channel_id),
    )
    return row is not None


async def clear_whitelist(guild_id: int) -> int:
    cur = await db.execute("DELETE FROM whitelist WHERE guild_id = ?", (guild_id,))
    return cur.rowcount
