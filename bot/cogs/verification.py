"""
Verification cog - server entry verification system.

Features:
- Button-based verification (click to verify)
- Captcha-style verification (math/text)
- Age-based verification (account must be X days old)
- Auto-assign role on verify
- Failed attempt tracking
- Quarantine unverified users after threshold

Methods:
- 'button' - simple click-to-verify
- 'math' - solve a math problem
- 'reaction' - react with specific emoji
"""
from __future__ import annotations

import random
import discord
from discord.ext import commands
from discord.ui import View, Button
from typing import Optional

from ..core.config import config
from ..core.database import db
from ..core.permissions import is_admin
from ..core.logger import setup_logger
from ..utils.embeds import success, error, warning, info, security
from ..utils.helpers import safe_send, notify_owner

log = setup_logger("securitybot.verification")


class VerificationView(View):
    def __init__(self, cog: "VerificationCog", guild_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, emoji="✅", custom_id="securitybot_verify")
    async def verify_button(self, interaction: discord.Interaction, button: Button) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member) or member.guild.id != self.guild_id:
            await interaction.response.send_message("This verification is not for your server.", ephemeral=True)
            return

        # Check if already verified
        row = await db.fetchone(
            "SELECT verified_at FROM verifications WHERE guild_id = ? AND user_id = ?",
            (self.guild_id, member.id),
        )
        if row and row["verified_at"]:
            await interaction.response.send_message("You're already verified!", ephemeral=True)
            return

        # Get config
        cfg_row = await db.fetchone("SELECT * FROM verification_config WHERE guild_id = ?", (self.guild_id,))
        if not cfg_row or not cfg_row["enabled"]:
            await interaction.response.send_message("Verification is not enabled.", ephemeral=True)
            return

        # Check account age
        required_age = cfg_row["required_age_days"]
        if required_age > 0:
            account_age_days = (discord.utils.utcnow() - member.created_at).days
            if account_age_days < required_age:
                await interaction.response.send_message(
                    f"❌ Your account is too new. You need an account at least **{required_age} days old** (yours is {account_age_days} days).",
                    ephemeral=True,
                )
                return

        method = cfg_row["method"]
        if method == "button":
            await self.cog._verify_user(member, interaction=interaction)
        elif method == "math":
            await self.cog._start_math_verification(member, interaction)
        else:
            await self.cog._verify_user(member, interaction=interaction)


class MathVerificationView(View):
    def __init__(self, cog: "VerificationCog", member: discord.Member, answer: int) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.member = member
        self.answer = answer

    @discord.ui.button(label="Submit Answer", style=discord.ButtonStyle.primary, custom_id="math_submit")
    async def submit(self, interaction: discord.Interaction, button: Button) -> None:
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("This isn't your verification.", ephemeral=True)
            return
        # Use a modal for input
        await interaction.response.send_modal(MathAnswerModal(self.cog, self.member, self.answer))


class MathAnswerModal(discord.ui.Modal, title="Math Verification"):
    answer_input = discord.ui.TextInput(label="Your answer", placeholder="Enter the result", required=True)

    def __init__(self, cog: "VerificationCog", member: discord.Member, answer: int) -> None:
        super().__init__()
        self.cog = cog
        self.member = member
        self.answer = answer

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            user_answer = int(self.answer_input.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid number.", ephemeral=True)
            return

        if user_answer == self.answer:
            await self.cog._verify_user(self.member, interaction=interaction)
        else:
            # Increment failed attempts
            await db.execute(
                "UPDATE verifications SET attempts = attempts + 1 WHERE guild_id = ? AND user_id = ?",
                (self.member.guild.id, self.member.id),
            )
            await interaction.response.send_message("❌ Wrong answer. Try again.", ephemeral=True)
            await self.cog._check_failed_attempts(self.member)


class VerificationCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _verify_user(self, member: discord.Member, interaction: Optional[discord.Interaction] = None) -> None:
        import time
        # Mark verified
        await db.execute(
            """INSERT OR REPLACE INTO verifications (guild_id, user_id, verified_at, method)
               VALUES (?, ?, ?, 'button')""",
            (member.guild.id, member.id, int(time.time())),
        )

        # Assign role
        cfg_row = await db.fetchone("SELECT role_id FROM verification_config WHERE guild_id = ?", (member.guild.id,))
        if cfg_row and cfg_row["role_id"]:
            role = member.guild.get_role(cfg_row["role_id"])
            if role:
                try:
                    await member.add_roles(role, reason="Verified by SecurityBot")
                except discord.Forbidden:
                    pass

        # Log
        log_row = await db.fetchone("SELECT security_log FROM log_channels WHERE guild_id = ?", (member.guild.id,))
        if log_row and log_row["security_log"]:
            channel = member.guild.get_channel(log_row["security_log"])
            if channel:
                embed = success("Member Verified", f"<@{member.id}> (`{member.id}`) has been verified.")
                await safe_send(channel, embed=embed)

        if interaction:
            await interaction.response.send_message("✅ You've been verified!", ephemeral=True)

    async def _start_math_verification(self, member: discord.Member, interaction: discord.Interaction) -> None:
        a = random.randint(1, 20)
        b = random.randint(1, 20)
        answer = a + b
        # Ensure entry exists
        await db.execute(
            """INSERT OR IGNORE INTO verifications (guild_id, user_id, attempts) VALUES (?, ?, 0)""",
            (member.guild.id, member.id),
        )
        embed = info("Math Verification", f"Solve this to verify: **{a} + {b} = ?**")
        view = MathVerificationView(self, member, answer)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _check_failed_attempts(self, member: discord.Member) -> None:
        row = await db.fetchone(
            "SELECT attempts FROM verifications WHERE guild_id = ? AND user_id = ?",
            (member.guild.id, member.id),
        )
        if not row:
            return
        if row["attempts"] >= 5:
            # Quarantine after 5 failed attempts
            from ..utils.helpers import quarantine_member
            await quarantine_member(member, reason="5 failed verification attempts")
            await notify_owner(
                self.bot,
                warning(
                    "User Quarantined",
                    f"**Server:** {member.guild.name}\n**User:** <@{member.id}> (`{member.id}`)\n"
                    f"**Reason:** 5 failed verification attempts",
                ),
            )

    # ---------- Commands ----------

    @commands.hybrid_command(name="verification")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def verification_status(self, ctx: commands.Context) -> None:
        """View verification configuration."""
        row = await db.fetchone("SELECT * FROM verification_config WHERE guild_id = ?", (ctx.guild.id,))
        if not row:
            await ctx.send(embed=info("Not Configured", "Verification is not set up. Use `!setupverification`."))
            return
        embed = info("Verification Configuration", f"Enabled: `{bool(row['enabled'])}`")
        embed.add_field(name="Method", value=row["method"], inline=True)
        embed.add_field(name="Role", value=f"<@&{row['role_id']}>" if row["role_id"] else "None", inline=True)
        embed.add_field(name="Channel", value=f"<#{row['channel_id']}>" if row["channel_id"] else "None", inline=True)
        embed.add_field(name="Min Account Age", value=f"{row['required_age_days']} days", inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="setupverification")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def setup_verification(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        role: discord.Role,
        method: str = "button",
        min_age_days: int = 0,
    ) -> None:
        """Setup verification. Methods: button, math"""
        if method not in ("button", "math"):
            await ctx.send(embed=error("Invalid Method", "Method must be `button` or `math`."))
            return

        await db.execute(
            """INSERT OR REPLACE INTO verification_config
               (guild_id, enabled, role_id, channel_id, method, required_age_days)
               VALUES (?, 1, ?, ?, ?, ?)""",
            (ctx.guild.id, role.id, channel.id, method, max(0, min_age_days)),
        )

        # Send verification panel
        embed = security(
            "✅ Verification Required",
            f"Click the button below to verify and gain access to **{ctx.guild.name}**.\n\n"
            f"**Requirements:**\n"
            f"• Account age: {min_age_days}+ days" + ("" if min_age_days == 0 else f" (you have {(discord.utils.utcnow() - ctx.author.created_at).days})") + "\n"
            f"• Method: {method}",
        )
        embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)
        view = VerificationView(self, ctx.guild.id)
        await channel.send(embed=embed, view=view)
        await ctx.send(embed=success("Verification Setup", f"Verification panel sent to <#{channel.id}>."))

    @commands.hybrid_command(name="disableverification")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def disable_verification(self, ctx: commands.Context) -> None:
        await db.execute("UPDATE verification_config SET enabled = 0 WHERE guild_id = ?", (ctx.guild.id,))
        await ctx.send(embed=success("Disabled", "Verification has been disabled."))

    @commands.hybrid_command(name="verify")
    @commands.guild_only()
    async def verify_command(self, ctx: commands.Context) -> None:
        """Manually trigger verification."""
        row = await db.fetchone("SELECT * FROM verification_config WHERE guild_id = ? AND enabled = 1", (ctx.guild.id,))
        if not row:
            await ctx.send(embed=info("Not Enabled", "Verification is not enabled in this server."))
            return
        # Check if already verified
        verif = await db.fetchone(
            "SELECT verified_at FROM verifications WHERE guild_id = ? AND user_id = ?",
            (ctx.guild.id, ctx.author.id),
        )
        if verif and verif["verified_at"]:
            await ctx.send(embed=info("Already Verified", "You're already verified."))
            return

        if row["method"] == "math":
            await self._start_math_verification(ctx.author, ctx)
        else:
            await self._verify_user(ctx.author)
            await ctx.send(embed=success("Verified", "You've been verified!"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VerificationCog(bot))
