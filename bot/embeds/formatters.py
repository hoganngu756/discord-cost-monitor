"""
Discord embed formatters for the Kube Cost Bot.

Provides reusable builders that produce rich, color-coded Discord Embeds
for workload audits, cluster summaries, and weekly reports.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from metrics.analyzer import ClusterSummary, NamespaceAudit, WorkloadAudit

# ── Color Palette ────────────────────────────────────────────────────────────

COLOR_RED = discord.Color.from_rgb(231, 76, 60)       # Efficiency < 30%
COLOR_ORANGE = discord.Color.from_rgb(243, 156, 18)    # Efficiency 30-49%
COLOR_YELLOW = discord.Color.from_rgb(241, 196, 15)    # Efficiency 50-69%
COLOR_GREEN = discord.Color.from_rgb(46, 204, 113)     # Efficiency ≥ 70%
COLOR_BLUE = discord.Color.from_rgb(52, 152, 219)      # Informational
COLOR_PURPLE = discord.Color.from_rgb(155, 89, 182)    # Weekly report

# ── Helpers ──────────────────────────────────────────────────────────────────

_GIB = 1024 ** 3
_MIB = 1024 ** 2


def _efficiency_color(pct: float) -> discord.Color:
    """Return a color based on efficiency percentage."""
    if pct < 30:
        return COLOR_RED
    if pct < 50:
        return COLOR_ORANGE
    if pct < 70:
        return COLOR_YELLOW
    return COLOR_GREEN


def _efficiency_emoji(pct: float) -> str:
    """Return an emoji indicator for efficiency."""
    if pct < 30:
        return "🔴"
    if pct < 50:
        return "🟠"
    if pct < 70:
        return "🟡"
    return "🟢"


def _fmt_cores(cores: float) -> str:
    """Format CPU cores for display."""
    if cores < 0.01:
        return f"{cores * 1000:.0f}m"
    return f"{cores:.3f}"


def _fmt_mem(mem_bytes: float) -> str:
    """Format memory bytes for display."""
    if mem_bytes >= _GIB:
        return f"{mem_bytes / _GIB:.2f} GiB"
    return f"{mem_bytes / _MIB:.1f} MiB"


def _fmt_usd(amount: float) -> str:
    """Format USD amount."""
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"


# ── Workload Audit Embed ─────────────────────────────────────────────────────


def build_workload_embed(audit: WorkloadAudit, rank: int | None = None) -> discord.Embed:
    """Build a Discord embed for a single workload audit result."""
    title_prefix = f"#{rank} · " if rank else ""
    title = f"{title_prefix}{audit.workload_name}"

    eff = audit.overall_efficiency
    embed = discord.Embed(
        title=title,
        color=_efficiency_color(eff),
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )

    # Header description
    emoji = _efficiency_emoji(eff)
    status = "Over-provisioned ⚠️" if audit.is_overprovisioned else "Healthy ✅"
    embed.description = (
        f"{emoji} **Overall Efficiency: {eff:.1f}%** — {status}\n"
        f"**Type:** `{audit.workload_type}` · **Namespace:** `{audit.namespace}`"
    )

    # CPU field
    embed.add_field(
        name="⚡ CPU",
        value=(
            f"**Requested:** `{_fmt_cores(audit.cpu_requested)}`\n"
            f"**Actual Avg:** `{_fmt_cores(audit.cpu_actual_avg)}`\n"
            f"**Efficiency:** `{audit.cpu_efficiency:.1f}%`\n"
            f"**Recommended:** `{_fmt_cores(audit.cpu_recommended)}`"
        ),
        inline=True,
    )

    # Memory field
    embed.add_field(
        name="🧠 Memory",
        value=(
            f"**Requested:** `{_fmt_mem(audit.mem_requested_bytes)}`\n"
            f"**Actual Avg:** `{_fmt_mem(audit.mem_actual_avg_bytes)}`\n"
            f"**Efficiency:** `{audit.mem_efficiency:.1f}%`\n"
            f"**Recommended:** `{_fmt_mem(audit.mem_recommended_bytes)}`"
        ),
        inline=True,
    )

    # Cost field
    embed.add_field(
        name="💰 Estimated Daily Waste",
        value=f"**{_fmt_usd(audit.daily_waste_usd)}/day**",
        inline=False,
    )

    # Footer
    footer_parts = [f"Containers: {audit.container_count}"]
    if audit.fallback_pricing:
        footer_parts.append("⚠️ Cost estimated from local pricing (OpenCost unavailable)")
    embed.set_footer(text=" · ".join(footer_parts))

    return embed


# ── Namespace Audit Embed ────────────────────────────────────────────────────


def build_namespace_header_embed(audit: NamespaceAudit) -> discord.Embed:
    """Build a summary header embed for a namespace audit."""
    eff = audit.overall_efficiency
    embed = discord.Embed(
        title=f"📊 Namespace Audit: `{audit.namespace}`",
        color=_efficiency_color(eff),
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )

    emoji = _efficiency_emoji(eff)
    embed.description = (
        f"{emoji} **Overall Efficiency: {eff:.1f}%**\n\n"
        f"**Workloads Scanned:** {len(audit.workloads)}\n"
        f"**Containers Scanned:** {audit.scanned_containers}\n"
        f"**Total Daily Waste:** {_fmt_usd(audit.total_daily_waste_usd)}"
    )

    embed.add_field(
        name="⚡ Avg CPU Efficiency",
        value=f"`{audit.avg_cpu_efficiency:.1f}%`",
        inline=True,
    )
    embed.add_field(
        name="🧠 Avg Memory Efficiency",
        value=f"`{audit.avg_mem_efficiency:.1f}%`",
        inline=True,
    )

    if audit.fallback_pricing:
        embed.add_field(
            name="⚠️ Note",
            value="Costs estimated from local pricing profile (OpenCost unavailable)",
            inline=False,
        )

    return embed


# ── Cluster Summary Embed ────────────────────────────────────────────────────


def build_cluster_summary_embed(summary: ClusterSummary) -> discord.Embed:
    """Build a Discord embed for the cluster-wide summary."""
    eff = summary.overall_efficiency
    embed = discord.Embed(
        title="🏗️ Cluster Efficiency Summary",
        color=_efficiency_color(eff),
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )

    emoji = _efficiency_emoji(eff)
    embed.description = (
        f"{emoji} **Cluster-Wide Efficiency: {eff:.1f}%**\n\n"
        f"**Namespaces Scanned:** {summary.namespaces_scanned}\n"
        f"**Estimated Daily Waste:** {_fmt_usd(summary.total_daily_waste_usd)}\n"
        f"**Estimated Monthly Waste:** {_fmt_usd(summary.total_daily_waste_usd * 30)}"
    )

    embed.add_field(
        name="⚡ CPU Efficiency",
        value=f"`{summary.cluster_cpu_efficiency:.1f}%`",
        inline=True,
    )
    embed.add_field(
        name="🧠 Memory Efficiency",
        value=f"`{summary.cluster_mem_efficiency:.1f}%`",
        inline=True,
    )

    # Top wasteful namespaces
    if summary.top_wasteful_namespaces:
        lines = []
        for i, (ns, waste) in enumerate(summary.top_wasteful_namespaces[:5], 1):
            lines.append(f"`{i}.` **{ns}** — {_fmt_usd(waste)}/day")
        embed.add_field(
            name="🔥 Top Wasteful Namespaces",
            value="\n".join(lines),
            inline=False,
        )

    if summary.fallback_pricing:
        embed.set_footer(
            text="⚠️ Costs estimated from local pricing profile (OpenCost unavailable)"
        )

    return embed


# ── Weekly Report Embed ──────────────────────────────────────────────────────


def build_weekly_report_embeds(
    workloads: list[WorkloadAudit],
) -> list[discord.Embed]:
    """Build a set of embeds for the weekly automated report.

    Returns a header embed followed by one embed per workload.
    """
    header = discord.Embed(
        title="📋 Weekly Efficiency Report",
        description=(
            f"**Top {len(workloads)} most inefficient workloads** across the cluster.\n"
            "Review these workloads for potential right-sizing opportunities."
        ),
        color=COLOR_PURPLE,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    header.set_footer(text="Automated report · Kube Cost Bot")

    embeds = [header]
    for i, wl in enumerate(workloads, 1):
        embeds.append(build_workload_embed(wl, rank=i))

    return embeds


# ── Error Embed ──────────────────────────────────────────────────────────────


def build_error_embed(title: str, description: str) -> discord.Embed:
    """Build a standardized error embed."""
    return discord.Embed(
        title=f"❌ {title}",
        description=description,
        color=COLOR_RED,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
