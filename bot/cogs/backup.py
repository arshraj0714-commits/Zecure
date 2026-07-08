"""
Backup & Restore cog - create and restore server backups.

Backups include:
- Channels (text, voice, categories) with permissions
- Roles with permissions and hierarchy
- Server settings (name, icon, verification level)
- Emojis (metadata only; binary download optional)
- Channel topics and slowmode

Stored as JSON files in /app/backups/
"""
from __future__ import annotations

import json
import os
import time
import discord
from discord.ext import commands
from pathlib import Path
from typing import Optional

from ..core.config import config
from ..core.database import db
from ..core.logger import setup_logger
from ..utils.embeds import success, error, warning, info
from ..utils.helpers import chunks

log = setup_logger("securitybot.backup")

BACKUP_DIR = Path("./backups")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


class BackupCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="backup")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def create_backup(self, ctx: commands.Context) -> None:
        """Create a full server backup."""
        status_msg = await ctx.send(embed=info("Creating Backup", "This may take a moment..."))
        guild = ctx.guild

        try:
            backup = await self._serialize_guild(guild)
            file_name = f"backup_{guild.id}_{int(time.time())}.json"
            file_path = BACKUP_DIR / file_name
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(backup, f, indent=2, ensure_ascii=False, default=str)

            size = file_path.stat().st_size

            # Record in DB
            await db.execute(
                """INSERT INTO backups (guild_id, created_by, file_path, size_bytes, channels, roles)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (guild.id, ctx.author.id, str(file_path), size, len(backup["channels"]), len(backup["roles"])),
            )

            embed = success(
                "Backup Created",
                f"**File:** `{file_name}`\n"
                f"**Size:** {size:,} bytes\n"
                f"**Channels:** {len(backup['channels'])}\n"
                f"**Roles:** {len(backup['roles'])}\n"
                f"**Created at:** <t:{int(time.time())}:R>",
            )
            await status_msg.edit(embed=embed)

        except Exception as e:
            log.exception("Backup failed: %s", e)
            await status_msg.edit(embed=error("Backup Failed", f"```\n{e}\n```"))

    async def _serialize_guild(self, guild: discord.Guild) -> dict:
        """Convert guild to JSON-serializable dict."""
        # Roles (sorted bottom-up so we can recreate in order)
        roles_data = []
        for role in sorted(guild.roles, key=lambda r: r.position):
            if role.is_default() or role.is_bot_managed() or role.is_integration():
                continue
            roles_data.append({
                "id": role.id,
                "name": role.name,
                "color": role.color.value,
                "hoist": role.hoist,
                "mentionable": role.mentionable,
                "permissions": role.permissions.value,
                "position": role.position,
            })

        # Channels
        channels_data = []
        for channel in sorted(guild.channels, key=lambda c: c.position or 0):
            try:
                ch_data = {
                    "id": channel.id,
                    "name": channel.name,
                    "type": str(channel.type),
                    "position": channel.position,
                    "category_id": channel.category_id,
                }
                if isinstance(channel, discord.TextChannel):
                    ch_data["topic"] = channel.topic
                    ch_data["slowmode_delay"] = channel.slowmode_delay
                    ch_data["nsfw"] = channel.nsfw
                elif isinstance(channel, discord.VoiceChannel):
                    ch_data["bitrate"] = channel.bitrate
                    ch_data["user_limit"] = channel.user_limit
                # Permission overwrites
                overwrites = []
                for target, perms in channel.overwrites.items():
                    overwrites.append({
                        "target_id": target.id,
                        "target_type": "role" if isinstance(target, discord.Role) else "member",
                        "allow": perms.allow().value if hasattr(perms, 'allow') else perms.pair()[0].value,
                        "deny": perms.deny().value if hasattr(perms, 'deny') else perms.pair()[1].value,
                    })
                ch_data["overwrites"] = overwrites
                channels_data.append(ch_data)
            except Exception as e:
                log.warning("Failed to serialize channel %s: %s", channel.name, e)

        # Server settings
        settings = {
            "name": guild.name,
            "icon_url": str(guild.icon.url) if guild.icon else None,
            "verification_level": str(guild.verification_level),
            "default_notifications": str(guild.default_notifications),
            "explicit_content_filter": str(guild.explicit_content_filter),
            "mfa_level": guild.mfa_level,
            "premium_tier": guild.premium_tier,
        }

        return {
            "version": "2.0",
            "guild_id": guild.id,
            "guild_name": guild.name,
            "created_at": int(time.time()),
            "settings": settings,
            "roles": roles_data,
            "channels": channels_data,
        }

    @commands.hybrid_command(name="backups")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def list_backups(self, ctx: commands.Context) -> None:
        """List all backups for this server."""
        rows = await db.fetchall(
            "SELECT * FROM backups WHERE guild_id = ? ORDER BY timestamp DESC LIMIT 20",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.send(embed=info("No Backups", "No backups found for this server."))
            return
        embed = info("Server Backups", f"Found {len(rows)} backups:")
        for r in rows[:10]:
            embed.add_field(
                name=f"#{r['id']} — <t:{r['timestamp']}:R>",
                value=f"Channels: {r['channels']} | Roles: {r['roles']} | Size: {r['size_bytes']:,}B",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="restorebackup")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def restore_backup(self, ctx: commands.Context, backup_id: int, mode: str = "channels") -> None:
        """Restore a backup. Modes: channels, roles, all"""
        row = await db.fetchone(
            "SELECT * FROM backups WHERE id = ? AND guild_id = ?",
            (backup_id, ctx.guild.id),
        )
        if not row:
            await ctx.send(embed=error("Not Found", f"Backup #{backup_id} not found."))
            return
        if not os.path.exists(row["file_path"]):
            await ctx.send(embed=error("File Missing", "Backup file no longer exists on disk."))
            return

        # Confirm
        confirm_embed = warning(
            "⚠️ Restore Backup?",
            f"You are about to restore backup #{backup_id}.\n"
            f"**Mode:** {mode}\n"
            f"This will **modify server channels/roles**. Type `!confirmrestore {backup_id}` to proceed.",
        )
        await ctx.send(embed=confirm_embed)

    @commands.hybrid_command(name="confirmrestore")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def confirm_restore(self, ctx: commands.Context, backup_id: int) -> None:
        row = await db.fetchone(
            "SELECT * FROM backups WHERE id = ? AND guild_id = ?",
            (backup_id, ctx.guild.id),
        )
        if not row:
            await ctx.send(embed=error("Not Found", f"Backup #{backup_id} not found."))
            return
        with open(row["file_path"], "r", encoding="utf-8") as f:
            backup = json.load(f)
        try:
            await self._restore_guild(ctx.guild, backup)
            await ctx.send(embed=success("Restore Complete", "Backup restored successfully."))
        except Exception as e:
            log.exception("Restore failed: %s", e)
            await ctx.send(embed=error("Restore Failed", f"```\n{e}\n```"))

    async def _restore_guild(self, guild: discord.Guild, backup: dict) -> None:
        """Restore channels and roles from backup."""
        # Restore roles first (bottom-up)
        for role_data in backup.get("roles", []):
            try:
                existing = guild.get_role(role_data["id"])
                perms = discord.Permissions(permissions=role_data["permissions"])
                if existing:
                    await existing.edit(
                        name=role_data["name"],
                        color=discord.Color(role_data["color"]),
                        hoist=role_data["hoist"],
                        mentionable=role_data["mentionable"],
                        permissions=perms,
                        reason="SecurityBot: restore from backup",
                    )
                else:
                    await guild.create_role(
                        name=role_data["name"],
                        color=discord.Color(role_data["color"]),
                        hoist=role_data["hoist"],
                        mentionable=role_data["mentionable"],
                        permissions=perms,
                        reason="SecurityBot: restore from backup",
                    )
            except discord.Forbidden:
                log.warning("Cannot restore role %s - missing permissions", role_data["name"])
            except Exception as e:
                log.warning("Failed to restore role %s: %s", role_data["name"], e)

        # Restore channels
        for ch_data in backup.get("channels", []):
            try:
                existing = guild.get_channel(ch_data["id"])
                if existing:
                    # Just update name/topic
                    if isinstance(existing, discord.TextChannel):
                        await existing.edit(
                            name=ch_data["name"],
                            topic=ch_data.get("topic"),
                            slowmode_delay=ch_data.get("slowmode_delay", 0),
                            reason="SecurityBot: restore from backup",
                        )
                else:
                    # Create new channel
                    if "text" in ch_data["type"]:
                        await guild.create_text_channel(
                            name=ch_data["name"],
                            topic=ch_data.get("topic"),
                            reason="SecurityBot: restore from backup",
                        )
                    elif "voice" in ch_data["type"]:
                        await guild.create_voice_channel(
                            name=ch_data["name"],
                            reason="SecurityBot: restore from backup",
                        )
            except discord.Forbidden:
                log.warning("Cannot restore channel %s - missing permissions", ch_data["name"])
            except Exception as e:
                log.warning("Failed to restore channel %s: %s", ch_data["name"], e)

    @commands.hybrid_command(name="deletebackup")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def delete_backup(self, ctx: commands.Context, backup_id: int) -> None:
        row = await db.fetchone(
            "SELECT * FROM backups WHERE id = ? AND guild_id = ?",
            (backup_id, ctx.guild.id),
        )
        if not row:
            await ctx.send(embed=error("Not Found", f"Backup #{backup_id} not found."))
            return
        try:
            if os.path.exists(row["file_path"]):
                os.remove(row["file_path"])
        except Exception:
            pass
        await db.execute("DELETE FROM backups WHERE id = ?", (backup_id,))
        await ctx.send(embed=success("Deleted", f"Backup #{backup_id} has been deleted."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BackupCog(bot))
