"""
Bot entrypoint — initializes the Discord bot, loads cogs, and configures
global error handling and logging.
"""

from __future__ import annotations

import logging
import sys

import discord
from discord.ext import commands

from config.settings import get_settings

logger = logging.getLogger(__name__)


class KubeCostBot(commands.Bot):
    """Custom bot subclass with lifecycle hooks for the Kube Cost Bot."""

    def __init__(self, settings) -> None:
        intents = discord.Intents.default()
        super().__init__(
            command_prefix="!",  # Not used — we use slash commands only
            intents=intents,
        )
        self.settings = settings

    async def setup_hook(self) -> None:
        """Load all cogs and sync slash commands on startup."""
        cog_modules = [
            "bot.cogs.audit",
            "bot.cogs.summary",
            "bot.cogs.scheduler",
        ]
        for module in cog_modules:
            try:
                await self.load_extension(module)
                logger.info("Loaded cog: %s", module)
            except Exception:
                logger.exception("Failed to load cog: %s", module)

        # Sync commands to the configured guild for instant availability
        guild = discord.Object(id=self.settings.discord_guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        logger.info("Slash commands synced to guild %s", self.settings.discord_guild_id)

    async def on_ready(self) -> None:
        """Log when the bot is connected and ready."""
        logger.info(
            "Bot connected as %s (ID: %s)", self.user, self.user.id  # type: ignore[union-attr]
        )
        logger.info("Connected to %d guild(s)", len(self.guilds))


# ── Global Error Handler ────────────────────────────────────────────────────


async def on_app_command_error(
    interaction: discord.Interaction,
    error: discord.app_commands.AppCommandError,
) -> None:
    """Global error handler for all slash commands.

    Catches exceptions, logs them, and responds gracefully to the user
    without crashing the WebSocket connection.
    """
    from bot.embeds.formatters import build_error_embed

    logger.exception("Slash command error in /%s", interaction.command.name if interaction.command else "unknown", exc_info=error)

    # Unwrap the original exception if wrapped
    original = getattr(error, "original", error)

    if isinstance(original, ConnectionError):
        embed = build_error_embed(
            "Connection Error",
            "Could not reach Prometheus or OpenCost. Please check that the monitoring stack is running.",
        )
    elif isinstance(original, TimeoutError):
        embed = build_error_embed(
            "Timeout",
            "The metrics query timed out. The cluster may be under heavy load — try again shortly.",
        )
    else:
        embed = build_error_embed(
            "Unexpected Error",
            f"An unexpected error occurred:\n```\n{original}\n```\nThis has been logged for investigation.",
        )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        logger.exception("Failed to send error response to Discord")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    """Application entrypoint."""
    settings = get_settings()
    settings.configure_logging()

    logger.info("Starting Kube Cost Bot...")
    logger.info("Prometheus URL: %s", settings.prometheus_url)
    logger.info("OpenCost URL: %s", settings.opencost_url)
    logger.info("Report Channel: %s", settings.report_channel_id)
    logger.info("Lookback Window: %s", settings.lookback_window)

    bot = KubeCostBot(settings)
    bot.tree.on_error = on_app_command_error

    try:
        bot.run(settings.discord_token, log_handler=None)
    except discord.LoginFailure:
        logger.critical("Invalid Discord token — check your DISCORD_TOKEN env var")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception:
        logger.exception("Fatal error — bot shutting down")
        sys.exit(1)


if __name__ == "__main__":
    main()
