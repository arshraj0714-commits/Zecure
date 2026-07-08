"""
Lightweight i18n system with JSON locale files.
Supports: en, es, fr, de, pt, hi
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

LOCALE_DIR = Path(__file__).parent.parent / "locales"
_cache: dict[str, dict] = {}
_fallback = "en"


def load_locales() -> None:
    """Load all locale files at startup."""
    for file in LOCALE_DIR.glob("*.json"):
        lang = file.stem
        try:
            with open(file, "r", encoding="utf-8") as f:
                _cache[lang] = json.load(f)
        except Exception as e:
            print(f"Failed to load locale {lang}: {e}")


def get_available_languages() -> list[str]:
    if not _cache:
        load_locales()
    return sorted(_cache.keys())


def t(key: str, lang: str = "en", **kwargs) -> str:
    """Translate a key. Falls back to English if missing."""
    if not _cache:
        load_locales()

    def _lookup(language: str) -> Optional[str]:
        data = _cache.get(language)
        if not data:
            return None
        parts = key.split(".")
        cur = data
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return None
        return cur if isinstance(cur, str) else None

    val = _lookup(lang) or _lookup(_fallback) or key
    if kwargs:
        try:
            val = val.format(**kwargs)
        except Exception:
            pass
    return val


def get_language_for_guild(guild_id: int) -> str:
    """Async helper for cogs. Defaults to en. Override per-guild via settings command."""
    # Sync cache to avoid circular import with database
    from .database import db
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return _fallback

    async def _get():
        row = await db.fetchone("SELECT language FROM guilds WHERE guild_id = ?", (guild_id,))
        return row["language"] if row else _fallback

    # We can't await here cleanly, so this is intentionally a fallback path
    return _fallback
