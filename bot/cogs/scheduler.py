"""
Cog: Scheduled weekly report

Posts a compilation of the top 3 most inefficient workloads to a
configured channel on a weekly schedule using ``discord.ext.tasks``.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from bot.embeds.formatters import build_error_embed, build_weekly_report_embeds
from config.settings import get_settings
from metrics.analyzer import ResourceAnalyzer

logger = logging.getLogger(__name__)

# Map day name → weekday int (0 = Monday)
_DAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


class SchedulerCog(commands.Cog):
    """Automated weekly efficiency report background task."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.analyzer = ResourceAnalyzer()
        self.settings = get_settings()

    async def cog_load(self) -> None:
        """Start the weekly task when the cog is loaded."""
        self.weekly_report.start()
        logger.info(
            "Scheduler started — reports every %s at %02d:00 UTC",
            self.settings.weekly_report_day.capitalize(),
            self.settings.weekly_report_hour,
        )

    async def cog_unload(self) -> None:
        """Stop the task and clean up resources."""
        self.weekly_report.cancel()
        await self.analyzer.close()

    @tasks.loop(hours=168)  # Runs once per week (7 * 24 = 168 hours)
    async def weekly_report(self) -> None:
        """Post the top 3 most inefficient workloads to the report channel."""
        logger.info("Weekly report task triggered")

        channel = self.bot.get_channel(self.settings.report_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(self.settings.report_channel_id)
            except discord.HTTPException:
                logger.error(
                    "Cannot find report channel %s — is the bot in the correct server?",
                    self.settings.report_channel_id,
                )
                return

        if not isinstance(channel, discord.TextChannel):
            logger.error("Report channel %s is not a text channel", self.settings.report_channel_id)
            return

        try:
            workloads = await self.analyzer.top_wasteful_workloads(n=3)
        except Exception:
            logger.exception("Failed to generate weekly report data")
            try:
                await channel.send(
                    embed=build_error_embed(
                        "Weekly Report Failed",
                        "Could not generate the weekly efficiency report. "
                        "Check bot logs for details.",
                    )
                )
            except discord.HTTPException:
                logger.exception("Failed to send error message to report channel")
            return

        if not workloads:
            embed = discord.Embed(
                title="📋 Weekly Efficiency Report",
                description=(
                    "✅ **No over-provisioned workloads detected!**\n\n"
                    "All workloads are within acceptable efficiency thresholds. "
                    "Great work on resource management!"
                ),
                color=discord.Color.from_rgb(46, 204, 113),
            )
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                logger.exception("Failed to send weekly report")
            return

        embeds = build_weekly_report_embeds(workloads)

        try:
            # Discord allows max 10 embeds per message
            await channel.send(embeds=embeds[:10])
            logger.info("Weekly report posted with %d workload(s)", len(workloads))
        except discord.HTTPException:
            logger.exception("Failed to send weekly report to channel")

    @weekly_report.before_loop
    async def before_weekly_report(self) -> None:
        """Wait until the bot is ready before starting the loop."""
        await self.bot.wait_until_ready()
        logger.info("Scheduler is ready — waiting for next scheduled time")

    @weekly_report.error
    async def weekly_report_error(self, error: Exception) -> None:
        """Log task errors without crashing the loop."""
        logger.exception("Weekly report task error (will retry next cycle)", exc_info=error)


async def setup(bot: commands.Bot) -> None:
    """Register this cog with the bot."""
    await bot.add_cog(SchedulerCog(bot))
