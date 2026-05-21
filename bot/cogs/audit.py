"""
Cog: /audit-namespace [namespace]

Scans a target namespace, identifies over-provisioned deployments, and
outputs formatted Discord embeds showing Requested vs. Actual usage
with right-sized configuration recommendations.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.embeds.formatters import (
    build_error_embed,
    build_namespace_header_embed,
    build_workload_embed,
)
from metrics.analyzer import ResourceAnalyzer

logger = logging.getLogger(__name__)

MAX_EMBEDS_PER_RESPONSE = 10  # Discord allows max 10 embeds per message


class AuditCog(commands.Cog):
    """Slash command for auditing a Kubernetes namespace."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.analyzer = ResourceAnalyzer()

    async def cog_unload(self) -> None:
        await self.analyzer.close()

    @app_commands.command(
        name="audit-namespace",
        description="Scan a namespace for over-provisioned workloads and show right-sizing recommendations.",
    )
    @app_commands.describe(
        namespace="The Kubernetes namespace to audit (e.g., 'default', 'production').",
    )
    async def audit_namespace(
        self,
        interaction: discord.Interaction,
        namespace: str,
    ) -> None:
        """Audit a single Kubernetes namespace."""
        await interaction.response.defer(ephemeral=True, thinking=True)

        logger.info(
            "User %s requested audit for namespace '%s'",
            interaction.user, namespace,
        )

        # Validate that the namespace exists
        try:
            known_namespaces = await self.analyzer._prom.get_namespace_list()
        except Exception:
            logger.exception("Failed to fetch namespace list")
            await interaction.followup.send(
                embed=build_error_embed(
                    "Prometheus Error",
                    "Could not reach Prometheus to validate the namespace. "
                    "Please ensure the monitoring stack is running.",
                ),
                ephemeral=True,
            )
            return

        if namespace not in known_namespaces:
            # Build a suggestion list
            suggestions = ", ".join(f"`{ns}`" for ns in known_namespaces[:15])
            await interaction.followup.send(
                embed=build_error_embed(
                    "Namespace Not Found",
                    f"No metrics found for namespace `{namespace}`.\n\n"
                    f"**Available namespaces:**\n{suggestions}",
                ),
                ephemeral=True,
            )
            return

        # Run the audit
        try:
            audit = await self.analyzer.audit_namespace(namespace)
        except Exception:
            logger.exception("Audit failed for namespace '%s'", namespace)
            await interaction.followup.send(
                embed=build_error_embed(
                    "Audit Failed",
                    f"An error occurred while auditing namespace `{namespace}`. "
                    "Check the bot logs for details.",
                ),
                ephemeral=True,
            )
            return

        # Build response embeds
        if not audit.workloads:
            await interaction.followup.send(
                embed=build_error_embed(
                    "No Workloads Found",
                    f"No container workloads with resource requests found in `{namespace}`.",
                ),
                ephemeral=True,
            )
            return

        embeds: list[discord.Embed] = [build_namespace_header_embed(audit)]
        for i, wl in enumerate(audit.workloads[:MAX_EMBEDS_PER_RESPONSE - 1], 1):
            embeds.append(build_workload_embed(wl, rank=i))

        # Add overflow notice if there are more workloads
        remaining = len(audit.workloads) - (MAX_EMBEDS_PER_RESPONSE - 1)
        if remaining > 0:
            embeds[-1].set_footer(
                text=f"... and {remaining} more workload(s) not shown."
            )

        await interaction.followup.send(embeds=embeds, ephemeral=True)

    @audit_namespace.autocomplete("namespace")
    async def namespace_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Provide autocomplete suggestions for namespace names."""
        try:
            namespaces = await self.analyzer._prom.get_namespace_list()
        except Exception:
            return []

        filtered = [
            ns for ns in namespaces
            if current.lower() in ns.lower()
        ]
        return [
            app_commands.Choice(name=ns, value=ns)
            for ns in filtered[:25]  # Discord max 25 autocomplete choices
        ]


async def setup(bot: commands.Bot) -> None:
    """Register this cog with the bot."""
    await bot.add_cog(AuditCog(bot))
