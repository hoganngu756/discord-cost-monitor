"""
Cog: /cluster-summary

Generates a high-level overview of overall cluster efficiency and
total estimated daily financial waste.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.embeds.formatters import build_cluster_summary_embed, build_error_embed
from metrics.analyzer import ResourceAnalyzer

logger = logging.getLogger(__name__)


class SummaryCog(commands.Cog):
    """Slash command for cluster-wide efficiency summary."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.analyzer = ResourceAnalyzer()

    async def cog_unload(self) -> None:
        await self.analyzer.close()

    @app_commands.command(
        name="cluster-summary",
        description="Show a high-level overview of cluster-wide resource efficiency and estimated daily waste.",
    )
    async def cluster_summary(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Generate a cluster-wide efficiency summary."""
        await interaction.response.defer(ephemeral=True, thinking=True)

        logger.info("User %s requested cluster summary", interaction.user)

        try:
            summary = await self.analyzer.cluster_summary()
        except Exception:
            logger.exception("Cluster summary generation failed")
            await interaction.followup.send(
                embed=build_error_embed(
                    "Summary Failed",
                    "An error occurred while generating the cluster summary. "
                    "Please ensure Prometheus is reachable and try again.",
                ),
                ephemeral=True,
            )
            return

        if summary.namespaces_scanned == 0:
            await interaction.followup.send(
                embed=build_error_embed(
                    "No Data",
                    "No namespaces with workload metrics were found. "
                    "Ensure that `kube-state-metrics` and `cAdvisor` are running.",
                ),
                ephemeral=True,
            )
            return

        embed = build_cluster_summary_embed(summary)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Register this cog with the bot."""
    await bot.add_cog(SummaryCog(bot))
