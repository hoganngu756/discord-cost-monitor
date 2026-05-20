"""
Application configuration via Pydantic BaseSettings.

All values are loaded from environment variables (or a .env file).
See .env.example for the full list of configurable parameters.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Base directory for the project (one level up from config/)
BASE_DIR = Path(__file__).resolve().parent.parent
PRICING_DIR = Path(__file__).resolve().parent / "pricing"


class Settings(BaseSettings):
    """Centralized, validated configuration for the Kube Cost Bot."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Discord ──────────────────────────────────────────────────────────
    discord_token: str = Field(
        ...,
        description="Discord bot authentication token.",
    )
    discord_guild_id: int = Field(
        ...,
        description="Discord server (guild) ID for slash command registration.",
    )
    report_channel_id: int = Field(
        ...,
        description="Channel ID where the weekly automated report is posted.",
    )

    # ── Prometheus ───────────────────────────────────────────────────────
    prometheus_url: str = Field(
        default="http://prometheus-server.monitoring.svc:9090",
        description="Base URL of the Prometheus server.",
    )

    # ── OpenCost ─────────────────────────────────────────────────────────
    opencost_url: str = Field(
        default="http://opencost.opencost.svc:9003",
        description="Base URL of the OpenCost API.",
    )

    # ── Pricing ──────────────────────────────────────────────────────────
    pricing_profile: str = Field(
        default="default.json",
        description="Filename of the pricing profile inside config/pricing/.",
    )

    # ── Analysis ─────────────────────────────────────────────────────────
    lookback_window: str = Field(
        default="24h",
        description="PromQL lookback window (e.g. 1h, 24h, 7d).",
    )
    efficiency_threshold: float = Field(
        default=50.0,
        description="Efficiency percentage below which a workload is flagged as over-provisioned.",
    )
    headroom_multiplier: float = Field(
        default=1.25,
        description="Safety margin multiplier for right-sizing recommendations.",
    )

    # ── Scheduler ────────────────────────────────────────────────────────
    weekly_report_day: str = Field(
        default="monday",
        description="Day of week (lowercase) for the automated weekly report.",
    )
    weekly_report_hour: int = Field(
        default=9,
        ge=0,
        le=23,
        description="Hour (0-23 UTC) to post the weekly report.",
    )

    # ── Logging ──────────────────────────────────────────────────────────
    log_level: str = Field(
        default="INFO",
        description="Python logging level.",
    )

    # ── Validators ───────────────────────────────────────────────────────

    @field_validator("weekly_report_day")
    @classmethod
    def validate_day(cls, v: str) -> str:
        valid_days = {
            "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday",
        }
        normalized = v.strip().lower()
        if normalized not in valid_days:
            raise ValueError(
                f"weekly_report_day must be one of {valid_days}, got '{v}'"
            )
        return normalized

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        normalized = v.strip().upper()
        if normalized not in valid_levels:
            raise ValueError(
                f"log_level must be one of {valid_levels}, got '{v}'"
            )
        return normalized

    @field_validator("lookback_window")
    @classmethod
    def validate_lookback_window(cls, v: str) -> str:
        """Ensure the window string follows PromQL duration format."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("lookback_window must not be empty")
        # Simple validation: last char must be a known unit
        valid_units = {"s", "m", "h", "d", "w", "y"}
        if stripped[-1] not in valid_units:
            raise ValueError(
                f"lookback_window must end with a valid unit ({valid_units}), got '{stripped}'"
            )
        # The numeric portion must be a positive integer
        numeric_part = stripped[:-1]
        if not numeric_part.isdigit() or int(numeric_part) <= 0:
            raise ValueError(
                f"lookback_window numeric portion must be a positive integer, got '{numeric_part}'"
            )
        return stripped

    # ── Helpers ──────────────────────────────────────────────────────────

    def load_pricing_profile(self) -> dict[str, Any]:
        """Load the JSON pricing profile from disk.

        Returns:
            Dict with keys like ``cpu_cost_per_core_hour`` and
            ``memory_cost_per_gib_hour``.

        Raises:
            FileNotFoundError: If the pricing file does not exist.
            json.JSONDecodeError: If the file is not valid JSON.
        """
        pricing_path = PRICING_DIR / self.pricing_profile
        if not pricing_path.exists():
            raise FileNotFoundError(
                f"Pricing profile not found: {pricing_path}"
            )
        with open(pricing_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("Loaded pricing profile from %s", pricing_path)
        return data

    def configure_logging(self) -> None:
        """Configure the root logger based on the ``log_level`` setting."""
        logging.basicConfig(
            level=self.log_level,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        # Suppress noisy third-party loggers
        logging.getLogger("discord").setLevel(logging.WARNING)
        logging.getLogger("discord.http").setLevel(logging.WARNING)
        logging.getLogger("aiohttp").setLevel(logging.WARNING)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton of the application settings.

    Using ``lru_cache`` ensures the .env file is read exactly once.
    """
    return Settings()  # type: ignore[call-arg]
