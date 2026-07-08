"""
Analytics cog - server metrics, security score, and dashboard data.

Commands:
- !securityscore - calculate and display the server's security score
- !serverstats - server statistics overview
- !memberstats - member growth and activity
- !modstats - moderation activity metrics
- !topmembers - top members by various criteria
"""
from __future__ import annotations

import discord
from discord.ext import commands
from typing import Optional
from collections import Counter

from ..core.database import db
from ..core.security_score import calculate_security_score
from ..core.logger import setup_logger
from ..utils.embeds import info, stats_embed, score_embed, error
from ..utils.constants import Colors, Icons
from ..utils.helpers import humanize_number

log = setup_logger("securitybot.analytics")


class AnalyticsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="securityscore", aliases=["score"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def security_score(self, ctx: commands.Context) -> None:
        """Calculate the server's security score (0-100)."""
        msg = await ctx.send(embed=info("Calculating...", "Analyzing server security posture."))
        score, factors = await calculate_security_score(ctx.guild)
        embed = score_embed(score, factors)
        embed.add_field(name="Server", value=ctx.guild.name, inline=False)
        await msg.edit(embed=embed)

    @commands.hybrid_command(name="serverstats", aliases=["stats"])
    @commands.guild_only()
    async def server_stats(self, ctx: commands.Context) -> None:
        """View server statistics."""
        g = ctx.guild
        members = g.member_count or 0
        online = sum(1 for m in g.members if m.status != discord.Status.offline) if not g.large else "Unknown (large server)"
        bots = sum(1 for m in g.members if m.bot)
        humans = members - bots
        channels_text = sum(1 for c in g.text_channels)
        channels_voice = sum(1 for c in g.voice_channels)
        channels_category = sum(1 for c in g.categories)
        embed = stats_embed(f"Server Stats — {g.name}", {
            f"{Icons.USER} Members": humanize_number(members),
            f"{Icons.USER} Humans": humanize_number(humans),
            f"{Icons.BOT} Bots": str(bots),
            f"{Icons.CHANNEL} Text Channels": str(channels_text),
            f"{Icons.CHANNEL} Voice Channels": str(channels_voice),
            f"{Icons.CHANNEL} Categories": str(channels_category),
            f"{Icons.ROLE} Roles": str(len(g.roles)),
            f"{Icons.CROWN} Boosts": f"Lvl {g.premium_tier} ({g.premium_subscription_count})",
            f"{Icons.CLOCK} Created": f"<t:{int(g.created_at.timestamp())}:R>",
        })
        embed.set_thumbnail(url=g.icon.url if g.icon else None)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="modstats")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def mod_stats(self, ctx: commands.Context) -> None:
        """View moderation activity metrics."""
        # Count actions in last 7/30 days
        import time
        now = int(time.time())
        last_7d = now - 86400 * 7
        last_30d = now - 86400 * 30

        actions_7d = await db.fetchall(
            "SELECT action, COUNT(*) as cnt FROM user_history WHERE guild_id = ? AND timestamp > ? GROUP BY action ORDER BY cnt DESC",
            (ctx.guild.id, last_7d),
        )
        actions_30d = await db.fetchall(
            "SELECT action, COUNT(*) as cnt FROM user_history WHERE guild_id = ? AND timestamp > ? GROUP BY action ORDER BY cnt DESC",
            (ctx.guild.id, last_30d),
        )

        incidents = await db.fetchall(
            "SELECT type, severity, COUNT(*) as cnt FROM incidents WHERE guild_id = ? AND timestamp > ? GROUP BY type, severity",
            (ctx.guild.id, last_30d),
        )

        embed = info(f"Moderation Stats — {ctx.guild.name}", "Last 7 days:")
        if actions_7d:
            for row in actions_7d:
                embed.add_field(name=row["action"], value=str(row["cnt"]), inline=True)
        else:
            embed.add_field(name="None", value="No actions in last 7 days", inline=False)

        # 30 day summary
        embed.add_field(name="\u200b", value="**Last 30 days:**", inline=False)
        if actions_30d:
            for row in actions_30d[:8]:
                embed.add_field(name=row["action"], value=str(row["cnt"]), inline=True)

        # Incidents
        if incidents:
            embed.add_field(name="\u200b", value="**Incidents (30d):**", inline=False)
            for inc in incidents:
                embed.add_field(name=f"{inc['type']} ({inc['severity']})", value=str(inc["cnt"]), inline=True)

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="topmembers")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def top_members(self, ctx: commands.Context, criterion: str = "joined") -> None:
        """Top members. Criteria: joined, oldest, newest"""
        if criterion == "joined":
            members = sorted([m for m in ctx.guild.members if m.joined_at], key=lambda m: m.joined_at)
            title = "Earliest Joiners"
            fmt = lambda m: f"<t:{int(m.joined_at.timestamp())}:R>"
        elif criterion == "oldest":
            members = sorted(ctx.guild.members, key=lambda m: m.created_at)
            title = "Oldest Accounts"
            fmt = lambda m: f"<t:{int(m.created_at.timestamp())}:R>"
        elif criterion == "newest":
            members = sorted(ctx.guild.members, key=lambda m: m.created_at, reverse=True)
            title = "Newest Accounts"
            fmt = lambda m: f"<t:{int(m.created_at.timestamp())}:R>"
        else:
            await ctx.send(embed=error("Invalid Criterion", "Use: joined, oldest, or newest"))
            return
        members = members[:10]
        embed = info(title, f"Top 10 for **{ctx.guild.name}**:")
        for i, m in enumerate(members, 1):
            embed.add_field(name=f"#{i} {m.display_name}", value=f"{m.mention}\n{fmt(m)}", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="scorehistory")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def score_history(self, ctx: commands.Context) -> None:
        """View security score history."""
        rows = await db.fetchall(
            "SELECT score, factors, timestamp FROM security_score_history WHERE guild_id = ? ORDER BY timestamp DESC LIMIT 10",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.send(embed=info("No History", "No security score history yet. Run `!securityscore` to create one."))
            return
        embed = info("Security Score History", "Last 10 scores:")
        for r in rows:
            emoji = "🟢" if r["score"] >= 80 else ("🟡" if r["score"] >= 50 else "🔴")
            embed.add_field(name=f"{emoji} {r['score']}/100", value=f"<t:{r['timestamp']}:R>", inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnalyticsCog(bot))
