"""
Async SQLite database layer with connection pooling.
"""
from __future__ import annotations

import aiosqlite
import os
from pathlib import Path
from typing import Any, Optional
from ..core.logger import setup_logger

log = setup_logger(__name__)

DB_PATH = "./data/security_bot.db"


class Database:
    def __init__(self, path: str = DB_PATH) -> None:
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.commit()
        log.info("Database connected: %s", self.path)
        await self._migrate()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            log.info("Database closed.")

    async def _migrate(self) -> None:
        """Create tables if they don't exist."""
        assert self._conn is not None
        statements = [
            # ---- Guilds & settings ----
            """CREATE TABLE IF NOT EXISTS guilds (
                guild_id    INTEGER PRIMARY KEY,
                prefix      TEXT NOT NULL DEFAULT '.',
                language    TEXT NOT NULL DEFAULT 'en',
                added_at    INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );""",
            """CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id    INTEGER PRIMARY KEY,
                settings    TEXT NOT NULL DEFAULT '{}',
                thresholds  TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );""",
            # ---- Whitelist ----
            """CREATE TABLE IF NOT EXISTS whitelist (
                guild_id    INTEGER NOT NULL,
                entity_id   INTEGER NOT NULL,
                entity_type TEXT NOT NULL CHECK(entity_type IN ('user','role','channel')),
                added_by    INTEGER NOT NULL,
                added_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                reason      TEXT,
                PRIMARY KEY (guild_id, entity_id, entity_type)
            );""",
            """CREATE TABLE IF NOT EXISTS whitelist_roles (
                guild_id    INTEGER NOT NULL,
                role_id     INTEGER NOT NULL,
                added_by    INTEGER NOT NULL,
                added_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (guild_id, role_id)
            );""",
            # ---- Admin roles ----
            """CREATE TABLE IF NOT EXISTS admin_roles (
                guild_id    INTEGER NOT NULL,
                role_id     INTEGER NOT NULL,
                added_by    INTEGER NOT NULL,
                added_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (guild_id, role_id)
            );""",
            # ---- Verification ----
            """CREATE TABLE IF NOT EXISTS verifications (
                guild_id    INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                verified_at INTEGER,
                method      TEXT,
                attempts    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );""",
            """CREATE TABLE IF NOT EXISTS verification_config (
                guild_id        INTEGER PRIMARY KEY,
                enabled         INTEGER NOT NULL DEFAULT 0,
                role_id         INTEGER,
                channel_id      INTEGER,
                log_channel_id  INTEGER,
                method          TEXT NOT NULL DEFAULT 'button',
                required_age_days INTEGER NOT NULL DEFAULT 0
            );""",
            # ---- Logging ----
            """CREATE TABLE IF NOT EXISTS log_channels (
                guild_id        INTEGER PRIMARY KEY,
                mod_log         INTEGER,
                server_log      INTEGER,
                member_log      INTEGER,
                voice_log       INTEGER,
                security_log    INTEGER,
                audit_log       INTEGER
            );""",
            """CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                actor_id    INTEGER,
                action      TEXT NOT NULL,
                target_id   INTEGER,
                target_type TEXT,
                reason      TEXT,
                metadata    TEXT,
                timestamp   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );""",
            """CREATE INDEX IF NOT EXISTS idx_audit_guild ON audit_log(guild_id, timestamp DESC);""",
            # ---- Incident reports ----
            """CREATE TABLE IF NOT EXISTS incidents (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                type        TEXT NOT NULL,
                severity    TEXT NOT NULL DEFAULT 'medium',
                description TEXT,
                actor_ids   TEXT,
                target_ids  TEXT,
                action_taken TEXT,
                resolved    INTEGER NOT NULL DEFAULT 0,
                timestamp   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                resolved_at INTEGER
            );""",
            """CREATE INDEX IF NOT EXISTS idx_incidents_guild ON incidents(guild_id, timestamp DESC);""",
            # ---- User history ----
            """CREATE TABLE IF NOT EXISTS user_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                action      TEXT NOT NULL,
                moderator_id INTEGER,
                reason      TEXT,
                duration    INTEGER,
                timestamp   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );""",
            """CREATE INDEX IF NOT EXISTS idx_history_user ON user_history(guild_id, user_id, timestamp DESC);""",
            # ---- AntiNuke tracking ----
            """CREATE TABLE IF NOT EXISTS antinuke_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                event_type  TEXT NOT NULL,
                target_id   INTEGER,
                timestamp   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );""",
            """CREATE INDEX IF NOT EXISTS idx_antinuke_lookup ON antinuke_events(guild_id, user_id, event_type, timestamp DESC);""",
            # ---- AntiSpam tracking ----
            """CREATE TABLE IF NOT EXISTS spam_tracker (
                guild_id    INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                channel_id  INTEGER NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                last_message TEXT,
                last_timestamp INTEGER NOT NULL DEFAULT 0,
                violations  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, channel_id)
            );""",
            # ---- Backups ----
            """CREATE TABLE IF NOT EXISTS backups (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                created_by  INTEGER NOT NULL,
                file_path   TEXT,
                size_bytes  INTEGER,
                channels    INTEGER,
                roles       INTEGER,
                timestamp   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );""",
            # ---- Scheduled tasks ----
            """CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                name        TEXT NOT NULL,
                action      TEXT NOT NULL,
                cron_expr   TEXT,
                run_at      INTEGER,
                last_run    INTEGER,
                next_run    INTEGER,
                enabled     INTEGER NOT NULL DEFAULT 1,
                payload     TEXT
            );""",
            # ---- Quarantine ----
            """CREATE TABLE IF NOT EXISTS quarantined_users (
                guild_id    INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                reason      TEXT,
                quarantined_by INTEGER,
                roles       TEXT,
                timestamp   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (guild_id, user_id)
            );""",
            # ---- Security score history ----
            """CREATE TABLE IF NOT EXISTS security_score_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                score       INTEGER NOT NULL,
                factors     TEXT,
                timestamp   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );""",
            """CREATE INDEX IF NOT EXISTS idx_score_guild ON security_score_history(guild_id, timestamp DESC);""",
            # ---- Raid tracking ----
            """CREATE TABLE IF NOT EXISTS raid_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                raid_type   TEXT NOT NULL,
                severity    TEXT NOT NULL DEFAULT 'medium',
                user_ids    TEXT,
                timestamp   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                resolved    INTEGER NOT NULL DEFAULT 0
            );""",
            # ---- Cypher-style AntiNuke config ----
            """CREATE TABLE IF NOT EXISTS antinuke_config (
                guild_id        INTEGER PRIMARY KEY,
                status          TEXT NOT NULL DEFAULT 'off',
                punishment      TEXT NOT NULL DEFAULT 'Ban',
                owners          TEXT NOT NULL DEFAULT '[]',
                whitelisted     TEXT NOT NULL DEFAULT '[]',
                antiban         TEXT NOT NULL DEFAULT 'on',
                antibot         TEXT NOT NULL DEFAULT 'on',
                antichannel     TEXT NOT NULL DEFAULT 'on',
                antiemoji       TEXT NOT NULL DEFAULT 'on',
                antiguild       TEXT NOT NULL DEFAULT 'on',
                antikick        TEXT NOT NULL DEFAULT 'on',
                antiping        TEXT NOT NULL DEFAULT 'on',
                antiprune       TEXT NOT NULL DEFAULT 'on',
                antirole        TEXT NOT NULL DEFAULT 'on',
                antiweb          TEXT NOT NULL DEFAULT 'on',
                antimember      TEXT NOT NULL DEFAULT 'on'
            );""",
            """CREATE TABLE IF NOT EXISTS antinuke_logs (
                guild_id        INTEGER PRIMARY KEY,
                channel_logs    INTEGER,
                mod_logs        INTEGER,
                guild_logs      INTEGER,
                role_logs       INTEGER
            );""",
            # ---- Cypher-style AutoMod ----
            """CREATE TABLE IF NOT EXISTS automod (
                guild_id    INTEGER PRIMARY KEY,
                enabled     INTEGER NOT NULL DEFAULT 0,
                log_channel INTEGER
            );""",
            """CREATE TABLE IF NOT EXISTS automod_punishments (
                guild_id    INTEGER NOT NULL,
                event       TEXT NOT NULL,
                punishment  TEXT NOT NULL DEFAULT 'Mute',
                PRIMARY KEY (guild_id, event)
            );""",
            """CREATE TABLE IF NOT EXISTS automod_ignored (
                guild_id    INTEGER NOT NULL,
                type        TEXT NOT NULL CHECK(type IN ('channel','role')),
                entity_id   INTEGER NOT NULL,
                PRIMARY KEY (guild_id, type, entity_id)
            );""",
        ]
        for stmt in statements:
            await self._conn.execute(stmt)
        await self._conn.commit()
        log.info("Database migrations complete.")

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        assert self._conn is not None
        cur = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cur

    async def executemany(self, sql: str, params_list) -> None:
        assert self._conn is not None
        await self._conn.executemany(sql, params_list)
        await self._conn.commit()

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[aiosqlite.Row]:
        assert self._conn is not None
        cur = await self._conn.execute(sql, params)
        return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        assert self._conn is not None
        cur = await self._conn.execute(sql, params)
        return await cur.fetchall()

    # ---------- Convenience helpers ----------
    async def _ensure_guild_exists(self, guild_id: int) -> None:
        """Ensure the guild row exists (foreign key safety)."""
        existing = await self.fetchone("SELECT 1 FROM guilds WHERE guild_id = ?", (guild_id,))
        if not existing:
            try:
                await self.execute(
                    "INSERT OR IGNORE INTO guilds (guild_id) VALUES (?)",
                    (guild_id,),
                )
            except Exception:
                pass  # race condition - another insert happened first

    async def get_guild_settings(self, guild_id: int) -> dict:
        row = await self.fetchone(
            "SELECT settings FROM guild_settings WHERE guild_id = ?", (guild_id,)
        )
        import json
        return json.loads(row["settings"]) if row else {}

    async def set_guild_settings(self, guild_id: int, settings: dict) -> None:
        import json
        await self._ensure_guild_exists(guild_id)
        existing = await self.fetchone(
            "SELECT 1 FROM guild_settings WHERE guild_id = ?", (guild_id,)
        )
        if existing:
            await self.execute(
                "UPDATE guild_settings SET settings = ? WHERE guild_id = ?",
                (json.dumps(settings), guild_id),
            )
        else:
            await self.execute(
                "INSERT INTO guild_settings (guild_id, settings, thresholds) VALUES (?, ?, '{}')",
                (guild_id, json.dumps(settings)),
            )

    async def get_thresholds(self, guild_id: int) -> dict:
        row = await self.fetchone(
            "SELECT thresholds FROM guild_settings WHERE guild_id = ?", (guild_id,)
        )
        import json
        if row and row["thresholds"] and row["thresholds"] != "{}":
            return json.loads(row["thresholds"])
        return {}  # caller should fall back to config defaults

    async def set_thresholds(self, guild_id: int, thresholds: dict) -> None:
        import json
        await self._ensure_guild_exists(guild_id)
        existing = await self.fetchone(
            "SELECT 1 FROM guild_settings WHERE guild_id = ?", (guild_id,)
        )
        if existing:
            await self.execute(
                "UPDATE guild_settings SET thresholds = ? WHERE guild_id = ?",
                (json.dumps(thresholds), guild_id),
            )
        else:
            await self.execute(
                "INSERT INTO guild_settings (guild_id, settings, thresholds) VALUES (?, '{}', ?)",
                (guild_id, json.dumps(thresholds)),
            )


# Singleton
db = Database()
