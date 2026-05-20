"""
Resource analyzer — combines Prometheus utilization data with OpenCost
financial metrics to produce actionable right-sizing recommendations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from config.settings import get_settings
from metrics.prometheus_client import PrometheusMetricsClient, ResourceUtilization
from metrics.opencost_client import OpenCostClient

logger = logging.getLogger(__name__)

# Bytes-to-human helpers
_GIB = 1024 ** 3
_MIB = 1024 ** 2


def bytes_to_mib(b: float) -> float:
    return round(b / _MIB, 1)


def bytes_to_gib(b: float) -> float:
    return round(b / _GIB, 3)


# ── Data Models ──────────────────────────────────────────────────────────────


@dataclass
class WorkloadAudit:
    """Complete audit result for a single workload."""
    namespace: str
    workload_name: str
    workload_type: str

    # CPU (cores)
    cpu_requested: float = 0.0
    cpu_actual_avg: float = 0.0
    cpu_efficiency: float = 0.0
    cpu_recommended: float = 0.0

    # Memory (bytes)
    mem_requested_bytes: float = 0.0
    mem_actual_avg_bytes: float = 0.0
    mem_efficiency: float = 0.0
    mem_recommended_bytes: float = 0.0

    # Cost (USD)
    daily_waste_usd: float = 0.0

    # Metadata
    container_count: int = 1
    is_overprovisioned: bool = False
    fallback_pricing: bool = False

    @property
    def overall_efficiency(self) -> float:
        return round((self.cpu_efficiency + self.mem_efficiency) / 2, 1)


@dataclass
class NamespaceAudit:
    """Audit results aggregated for a namespace."""
    namespace: str
    workloads: list[WorkloadAudit] = field(default_factory=list)
    total_daily_waste_usd: float = 0.0
    avg_cpu_efficiency: float = 0.0
    avg_mem_efficiency: float = 0.0
    scanned_containers: int = 0
    fallback_pricing: bool = False

    @property
    def overall_efficiency(self) -> float:
        return round((self.avg_cpu_efficiency + self.avg_mem_efficiency) / 2, 1)


@dataclass
class ClusterSummary:
    """High-level cluster-wide efficiency overview."""
    namespaces_scanned: int = 0
    total_daily_waste_usd: float = 0.0
    cluster_cpu_efficiency: float = 0.0
    cluster_mem_efficiency: float = 0.0
    top_wasteful_namespaces: list[tuple[str, float]] = field(default_factory=list)
    fallback_pricing: bool = False

    @property
    def overall_efficiency(self) -> float:
        return round((self.cluster_cpu_efficiency + self.cluster_mem_efficiency) / 2, 1)


# ── Analyzer ─────────────────────────────────────────────────────────────────


class ResourceAnalyzer:
    """Combines Prometheus metrics and OpenCost data into recommendations."""

    def __init__(
        self,
        prom_client: PrometheusMetricsClient | None = None,
        cost_client: OpenCostClient | None = None,
    ) -> None:
        self._prom = prom_client or PrometheusMetricsClient()
        self._cost = cost_client or OpenCostClient()
        self._settings = get_settings()

    async def close(self) -> None:
        await self._cost.close()

    async def audit_namespace(self, namespace: str) -> NamespaceAudit:
        """Perform a full resource audit on a single namespace."""
        logger.info("Starting audit for namespace '%s'", namespace)

        utilizations = await self._prom.get_resource_utilization(
            namespace, self._settings.lookback_window,
        )

        # Try OpenCost first
        opencost_workloads = await self._cost.get_workload_costs(namespace)
        opencost_map: dict[str, Any] = {
            w.workload_name: w for w in opencost_workloads
        }
        using_fallback = self._cost.is_using_fallback

        # Group utilizations by workload
        workload_groups: dict[str, list[ResourceUtilization]] = {}
        for u in utilizations:
            key = u.workload_name or u.pod
            workload_groups.setdefault(key, []).append(u)

        audits: list[WorkloadAudit] = []
        for wl_name, containers in workload_groups.items():
            audit = self._build_workload_audit(
                wl_name, containers, opencost_map.get(wl_name), using_fallback,
            )
            audits.append(audit)

        # Sort by daily waste descending
        audits.sort(key=lambda a: a.daily_waste_usd, reverse=True)

        total_waste = sum(a.daily_waste_usd for a in audits)
        avg_cpu = _safe_avg([a.cpu_efficiency for a in audits])
        avg_mem = _safe_avg([a.mem_efficiency for a in audits])

        result = NamespaceAudit(
            namespace=namespace,
            workloads=audits,
            total_daily_waste_usd=round(total_waste, 4),
            avg_cpu_efficiency=round(avg_cpu, 1),
            avg_mem_efficiency=round(avg_mem, 1),
            scanned_containers=len(utilizations),
            fallback_pricing=using_fallback,
        )
        logger.info(
            "Audit complete for '%s': %d workloads, $%.4f/day waste",
            namespace, len(audits), total_waste,
        )
        return result

    async def cluster_summary(self) -> ClusterSummary:
        """Generate a high-level cluster-wide efficiency summary."""
        logger.info("Generating cluster summary")

        namespaces = await self._prom.get_namespace_list()
        if not namespaces:
            logger.warning("No namespaces found — is Prometheus reachable?")
            return ClusterSummary()

        # Try OpenCost for cluster-wide costs
        ns_costs = await self._cost.get_namespace_costs()
        using_fallback = self._cost.is_using_fallback

        ns_waste: list[tuple[str, float]] = []
        all_cpu_eff: list[float] = []
        all_mem_eff: list[float] = []
        total_waste = 0.0

        for ns in namespaces:
            # Skip system namespaces
            if ns.startswith("kube-") or ns in ("kube-system", "kube-public", "kube-node-lease"):
                continue

            try:
                audit = await self.audit_namespace(ns)
                ns_waste.append((ns, audit.total_daily_waste_usd))
                total_waste += audit.total_daily_waste_usd
                all_cpu_eff.append(audit.avg_cpu_efficiency)
                all_mem_eff.append(audit.avg_mem_efficiency)
            except Exception:
                logger.exception("Failed to audit namespace '%s'", ns)

        ns_waste.sort(key=lambda x: x[1], reverse=True)

        return ClusterSummary(
            namespaces_scanned=len(ns_waste),
            total_daily_waste_usd=round(total_waste, 4),
            cluster_cpu_efficiency=round(_safe_avg(all_cpu_eff), 1),
            cluster_mem_efficiency=round(_safe_avg(all_mem_eff), 1),
            top_wasteful_namespaces=ns_waste[:5],
            fallback_pricing=using_fallback,
        )

    async def top_wasteful_workloads(self, n: int = 3) -> list[WorkloadAudit]:
        """Return the top *n* most wasteful workloads across the cluster."""
        namespaces = await self._prom.get_namespace_list()
        all_audits: list[WorkloadAudit] = []

        for ns in namespaces:
            if ns.startswith("kube-"):
                continue
            try:
                audit = await self.audit_namespace(ns)
                all_audits.extend(audit.workloads)
            except Exception:
                logger.exception("Failed to audit namespace '%s'", ns)

        # Filter to over-provisioned only, sort by waste
        overprovisioned = [a for a in all_audits if a.is_overprovisioned]
        overprovisioned.sort(key=lambda a: a.daily_waste_usd, reverse=True)
        return overprovisioned[:n]

    # ── Internal Helpers ─────────────────────────────────────────────────

    def _build_workload_audit(
        self,
        workload_name: str,
        containers: list[ResourceUtilization],
        opencost_data: Any | None,
        using_fallback: bool,
    ) -> WorkloadAudit:
        """Build a WorkloadAudit from raw container metrics + optional OpenCost data."""
        headroom = self._settings.headroom_multiplier
        threshold = self._settings.efficiency_threshold

        # Aggregate across containers
        total_cpu_req = sum(c.cpu_requested for c in containers)
        total_cpu_act = sum(c.cpu_actual for c in containers)
        total_mem_req = sum(c.mem_requested for c in containers)
        total_mem_act = sum(c.mem_actual for c in containers)

        cpu_eff = (total_cpu_act / total_cpu_req * 100) if total_cpu_req > 0 else 100.0
        mem_eff = (total_mem_act / total_mem_req * 100) if total_mem_req > 0 else 100.0

        cpu_recommended = round(total_cpu_act * headroom, 3)
        mem_recommended = round(total_mem_act * headroom)

        # Ensure recommendations have a sane minimum
        cpu_recommended = max(cpu_recommended, 0.01)
        mem_recommended = max(mem_recommended, 16 * _MIB)

        # Cost calculation
        if opencost_data and not using_fallback:
            daily_waste = getattr(opencost_data, "daily_waste", 0.0)
        else:
            cpu_delta = max(0, total_cpu_req - total_cpu_act)
            mem_delta = max(0, total_mem_req - total_mem_act)
            daily_waste = self._cost.estimate_cost_from_delta(cpu_delta, mem_delta, hours=24.0)

        wl_type = containers[0].workload_type if containers else "unknown"
        is_over = min(cpu_eff, mem_eff) < threshold

        return WorkloadAudit(
            namespace=containers[0].namespace,
            workload_name=workload_name,
            workload_type=wl_type,
            cpu_requested=round(total_cpu_req, 4),
            cpu_actual_avg=round(total_cpu_act, 4),
            cpu_efficiency=round(min(cpu_eff, 100.0), 1),
            cpu_recommended=cpu_recommended,
            mem_requested_bytes=total_mem_req,
            mem_actual_avg_bytes=total_mem_act,
            mem_efficiency=round(min(mem_eff, 100.0), 1),
            mem_recommended_bytes=mem_recommended,
            daily_waste_usd=round(daily_waste, 4),
            container_count=len(containers),
            is_overprovisioned=is_over,
            fallback_pricing=using_fallback,
        )


def _safe_avg(values: list[float]) -> float:
    """Average that returns 0.0 for empty lists."""
    return sum(values) / len(values) if values else 0.0
