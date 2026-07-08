"""
Security score calculator.
Evaluates guild security posture based on multiple factors.
"""
from __future__ import annotations

import discord
from typing import Tuple
from .database import db
from .config import config


async def calculate_security_score(guild: discord.Guild) -> Tuple[int, dict]:
    """
    Return (score 0-100, factor_breakdown).
    """
    factors = {}
    score = 100

    # ---- Verification enabled? ----
    verif = await db.fetchone("SELECT enabled FROM verification_config WHERE guild_id = ?", (guild.id,))
    if verif and verif["enabled"]:
        factors["verification"] = "+10 (verification enabled)"
    else:
        score -= 10
        factors["verification"] = "-10 (no verification)"

    # ---- 2FA requirement for admin role? ----
    mfa = guild.mfa_level
    if mfa >= 1:
        factors["2fa"] = "+5 (2FA required for mods)"
    else:
        score -= 5
        factors["2fa"] = "-5 (2FA not required)"

    # ---- Logging channel configured? ----
    logs = await db.fetchone("SELECT * FROM log_channels WHERE guild_id = ?", (guild.id,))
    if logs:
        configured = sum(1 for v in dict(logs).values() if v and v != guild.id and isinstance(v, int))
        if configured >= 3:
            factors["logging"] = f"+5 ({configured} log channels)"
        else:
            score -= 5
            factors["logging"] = f"-5 (only {configured} log channels)"
    else:
        score -= 5
        factors["logging"] = "-5 (no log channels)"

    # ---- Whitelist size ----
    wl = await db.fetchall("SELECT 1 FROM whitelist WHERE guild_id = ?", (guild.id,))
    if 0 < len(wl) <= 10:
        factors["whitelist"] = "+5 (sensible whitelist)"
    elif len(wl) > 20:
        score -= 5
        factors["whitelist"] = f"-5 ({len(wl)} whitelisted - too broad)"
    else:
        factors["whitelist"] = f"0 ({len(wl)} whitelisted)"

    # ---- Admin count ----
    admin_count = sum(1 for m in guild.members if m.guild_permissions.administrator)
    if admin_count <= 3:
        factors["admins"] = f"+5 ({admin_count} admins - tight)"
    elif admin_count <= 8:
        factors["admins"] = f"0 ({admin_count} admins)"
    else:
        score -= 10
        factors["admins"] = f"-10 ({admin_count} admins - too many)"

    # ---- Bot count ----
    bot_count = sum(1 for m in guild.members if m.bot)
    if bot_count <= 5:
        factors["bots"] = f"+5 ({bot_count} bots)"
    elif bot_count <= 15:
        factors["bots"] = f"0 ({bot_count} bots)"
    else:
        score -= 5
        factors["bots"] = f"-5 ({bot_count} bots - high)"

    # ---- Verification level ----
    vl = guild.verification_level
    if str(vl) in ("high", "highest"):
        factors["verification_level"] = f"+5 ({vl})"
    elif str(vl) == "medium":
        factors["verification_level"] = f"0 ({vl})"
    else:
        score -= 5
        factors["verification_level"] = f"-5 ({vl} - too low)"

    # ---- Recent incidents ----
    recent_incidents = await db.fetchall(
        "SELECT severity FROM incidents WHERE guild_id = ? AND timestamp > ? AND resolved = 0",
        (guild.id, __import__("time").time() - 86400 * 7),
    )
    if recent_incidents:
        critical_count = sum(1 for i in recent_incidents if i["severity"] == "critical")
        if critical_count:
            score -= 10
            factors["recent_incidents"] = f"-10 ({critical_count} critical in last 7d)"
        else:
            score -= 3
            factors["recent_incidents"] = f"-3 ({len(recent_incidents)} incidents in last 7d)"
    else:
        factors["recent_incidents"] = "+5 (no incidents in 7d)"
        score += 5

    score = max(0, min(100, score))

    # Store history
    import json
    await db.execute(
        "INSERT INTO security_score_history (guild_id, score, factors) VALUES (?, ?, ?)",
        (guild.id, score, json.dumps(factors)),
    )

    return score, factors
