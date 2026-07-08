"""
Permission system for the security bot.
Layers (highest priority first):
1. Bot owner (OWNER_ID from env)
2. Guild owner
3. Users in whitelist
4. Users with admin role (set via command)
5. Users with Discord Administrator permission
"""
from __future__ import annotations

import discord
from .database import db
from .config import config
from .logger import setup_logger

log = setup_logger(__name__)


class PermissionLevel:
    OWNER = "owner"
    ADMIN = "admin"
    WHITELISTED = "whitelisted"
    NONE = "none"


async def get_permission_level(member: discord.Member) -> str:
    """Return the highest permission level the member holds."""
    # 1. Bot owner
    if member.id == config.owner_id:
        return PermissionLevel.OWNER

    # 2. Guild owner
    if member.guild.owner_id == member.id:
        return PermissionLevel.OWNER

    # 3. Whitelisted user
    row = await db.fetchone(
        "SELECT 1 FROM whitelist WHERE guild_id = ? AND entity_id = ? AND entity_type = 'user'",
        (member.guild.id, member.id),
    )
    if row:
        return PermissionLevel.OWNER  # whitelisted users bypass all protection

    # 4. Admin role
    admin_roles = await db.fetchall(
        "SELECT role_id FROM admin_roles WHERE guild_id = ?", (member.guild.id,)
    )
    admin_role_ids = {r["role_id"] for r in admin_roles}
    if any(role.id in admin_role_ids for role in member.roles):
        return PermissionLevel.ADMIN

    # 5. Discord Administrator permission
    if member.guild_permissions.administrator:
        return PermissionLevel.ADMIN

    return PermissionLevel.NONE


async def is_owner(member: discord.Member) -> bool:
    return member.id == config.owner_id or member.guild.owner_id == member.id


async def is_admin(member: discord.Member) -> bool:
    level = await get_permission_level(member)
    return level in (PermissionLevel.OWNER, PermissionLevel.ADMIN)


async def is_whitelisted(member: discord.Member) -> bool:
    """User is whitelisted (bypasses all security checks)."""
    row = await db.fetchone(
        "SELECT 1 FROM whitelist WHERE guild_id = ? AND entity_id = ? AND entity_type = 'user'",
        (member.guild.id, member.id),
    )
    if row:
        return True
    # Check whitelisted roles
    whitelisted_role_rows = await db.fetchall(
        "SELECT role_id FROM whitelist WHERE guild_id = ? AND entity_type = 'role'",
        (member.guild.id,),
    )
    whitelisted_role_ids = {r["role_id"] for r in whitelisted_role_rows}
    return any(role.id in whitelisted_role_ids for role in member.roles)


async def is_action_allowed(member: discord.Member, action: str = "") -> bool:
    """Return True if the member is allowed to perform protected actions."""
    return await is_whitelisted(member) or await is_owner(member)


def has_discord_admin(member: discord.Member) -> bool:
    return member.guild_permissions.administrator
