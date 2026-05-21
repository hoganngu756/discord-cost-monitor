"""
Prometheus metrics client for querying container resource utilization.

Wraps the synchronous ``prometheus-api-client`` library in async helpers
so it can be called safely from the Discord event loop without blocking.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from prometheus_api_client import PrometheusConnect
from prometheus_api_client.exceptions import PrometheusApiClientException

from config.settings import get_settings

logger = logging.getLogger(__name__)


# ── Data Models ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContainerMetric:
    """Resource metric for a single container within a pod."""

    namespace: str
    pod: str
    container: str
    value: float  # cores (CPU) or bytes (memory)

    # Optional owner reference metadata (populated when available)
    workload_name: str = ""
    workload_type: str = ""  # e.g. "deployment", "statefulset", "daemonset"


@dataclass
class ResourceUtilization:
    """Paired requested vs. actual metrics for a single container."""

    namespace: str
    pod: str
    container: str
    workload_name: str
    workload_type: str

    cpu_requested: float = 0.0   # cores
    cpu_actual: float = 0.0      # cores
    mem_requested: float = 0.0   # bytes
    mem_actual: float = 0.0      # bytes

    @property
    def cpu_efficiency(self) -> float:
        """CPU efficiency as a percentage (0-100)."""
        if self.cpu_requested <= 0:
            return 100.0
        return min((self.cpu_actual / self.cpu_requested) * 100, 100.0)

    @property
    def mem_efficiency(self) -> float:
        """Memory efficiency as a percentage (0-100)."""
        if self.mem_requested <= 0:
            return 100.0
        return min((self.mem_actual / self.mem_requested) * 100, 100.0)


# ── PromQL Templates ────────────────────────────────────────────────────────

# Actual CPU usage rate averaged over the lookback window
_CPU_ACTUAL_QUERY = (
    'avg by (namespace, pod, container) ('
    '  rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[{window}])'
    ')'
)

# Requested CPU cores
_CPU_REQUESTED_QUERY = (
    'kube_pod_container_resource_requests{{'
    '  namespace="{namespace}", resource="cpu"'
    '}}'
)

# Actual memory working set
_MEM_ACTUAL_QUERY = (
    'avg_over_time('
    '  container_memory_working_set_bytes{{namespace="{namespace}"}}[{window}]'
    ')'
)

# Requested memory bytes
_MEM_REQUESTED_QUERY = (
    'kube_pod_container_resource_requests{{'
    '  namespace="{namespace}", resource="memory"'
    '}}'
)

# List all active namespaces
_NAMESPACE_LIST_QUERY = 'kube_namespace_status_phase{phase="Active"}'

# Owner (workload) info — maps pod to its controller
_WORKLOAD_OWNER_QUERY = (
    'kube_pod_owner{{namespace="{namespace}"}}'
)


# ── Client ───────────────────────────────────────────────────────────────────


class PrometheusMetricsClient:
    """Async-friendly wrapper around ``PrometheusConnect``.

    All PromQL queries are executed in a thread pool via
    ``asyncio.to_thread()`` to avoid blocking the event loop.
    """

    def __init__(self, url: str | None = None) -> None:
        settings = get_settings()
        self._url = url or settings.prometheus_url
        self._prom = PrometheusConnect(url=self._url, disable_ssl=True)
        logger.info("PrometheusMetricsClient initialized → %s", self._url)

    # ── Public API ───────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Return ``True`` if Prometheus is reachable."""
        try:
            result = await asyncio.to_thread(self._prom.check_prometheus_connection)
            return bool(result)
        except Exception:
            logger.exception("Prometheus health check failed")
            return False

    async def get_namespace_list(self) -> list[str]:
        """Return a list of active Kubernetes namespaces."""
        try:
            results = await self._query(_NAMESPACE_LIST_QUERY)
            namespaces = sorted(
                {r["metric"].get("namespace", "") for r in results} - {""}
            )
            logger.debug("Discovered %d namespaces", len(namespaces))
            return namespaces
        except Exception:
            logger.exception("Failed to list namespaces")
            return []

    async def get_cpu_utilization(
        self, namespace: str, window: str | None = None,
    ) -> list[ContainerMetric]:
        """Query actual CPU usage rate for all containers in *namespace*."""
        window = window or get_settings().lookback_window
        query = _CPU_ACTUAL_QUERY.format(namespace=namespace, window=window)
        return await self._query_to_metrics(query, namespace, metric_type="cpu")

    async def get_cpu_requests(self, namespace: str) -> list[ContainerMetric]:
        """Query requested CPU cores for all containers in *namespace*."""
        query = _CPU_REQUESTED_QUERY.format(namespace=namespace)
        return await self._query_to_metrics(query, namespace, metric_type="cpu")

    async def get_memory_utilization(
        self, namespace: str, window: str | None = None,
    ) -> list[ContainerMetric]:
        """Query actual memory working-set bytes for all containers in *namespace*."""
        window = window or get_settings().lookback_window
        query = _MEM_ACTUAL_QUERY.format(namespace=namespace, window=window)
        return await self._query_to_metrics(query, namespace, metric_type="memory")

    async def get_memory_requests(self, namespace: str) -> list[ContainerMetric]:
        """Query requested memory bytes for all containers in *namespace*."""
        query = _MEM_REQUESTED_QUERY.format(namespace=namespace)
        return await self._query_to_metrics(query, namespace, metric_type="memory")

    async def get_workload_owners(self, namespace: str) -> dict[str, tuple[str, str]]:
        """Return a mapping of ``pod_name → (owner_name, owner_kind)``.

        Used to correlate per-pod metrics back to their parent Deployment,
        StatefulSet, DaemonSet, etc.
        """
        query = _WORKLOAD_OWNER_QUERY.format(namespace=namespace)
        try:
            results = await self._query(query)
        except Exception:
            logger.exception("Failed to query workload owners for %s", namespace)
            return {}

        owners: dict[str, tuple[str, str]] = {}
        for r in results:
            metric = r.get("metric", {})
            pod = metric.get("pod", "")
            owner_name = metric.get("owner_name", "")
            owner_kind = metric.get("owner_kind", "").lower()

            # Skip ReplicaSet — resolve up to Deployment later in the analyzer
            if pod and owner_name:
                owners[pod] = (owner_name, owner_kind)

        return owners

    async def get_resource_utilization(
        self, namespace: str, window: str | None = None,
    ) -> list[ResourceUtilization]:
        """Full paired resource utilization for every container in *namespace*.

        Combines CPU/memory requests and actuals, enriched with workload
        ownership data, into a single list of ``ResourceUtilization`` objects.
        """
        # Fire all queries concurrently
        cpu_actual_task = self.get_cpu_utilization(namespace, window)
        cpu_req_task = self.get_cpu_requests(namespace)
        mem_actual_task = self.get_memory_utilization(namespace, window)
        mem_req_task = self.get_memory_requests(namespace)
        owners_task = self.get_workload_owners(namespace)

        (
            cpu_actuals,
            cpu_requests,
            mem_actuals,
            mem_requests,
            owners,
        ) = await asyncio.gather(
            cpu_actual_task,
            cpu_req_task,
            mem_actual_task,
            mem_req_task,
            owners_task,
        )

        # Index by (pod, container) for O(1) lookups
        cpu_actual_map = {(m.pod, m.container): m.value for m in cpu_actuals}
        cpu_req_map = {(m.pod, m.container): m.value for m in cpu_requests}
        mem_actual_map = {(m.pod, m.container): m.value for m in mem_actuals}
        mem_req_map = {(m.pod, m.container): m.value for m in mem_requests}

        # Union of all container keys
        all_keys = (
            set(cpu_actual_map) | set(cpu_req_map)
            | set(mem_actual_map) | set(mem_req_map)
        )

        results: list[ResourceUtilization] = []
        for pod, container in all_keys:
            # Skip pause containers and empty container names
            if not container or container == "POD":
                continue

            owner_name, owner_kind = owners.get(pod, ("unknown", "unknown"))

            results.append(
                ResourceUtilization(
                    namespace=namespace,
                    pod=pod,
                    container=container,
                    workload_name=owner_name,
                    workload_type=owner_kind,
                    cpu_requested=cpu_req_map.get((pod, container), 0.0),
                    cpu_actual=cpu_actual_map.get((pod, container), 0.0),
                    mem_requested=mem_req_map.get((pod, container), 0.0),
                    mem_actual=mem_actual_map.get((pod, container), 0.0),
                )
            )

        logger.info(
            "Collected resource utilization for %d containers in namespace '%s'",
            len(results), namespace,
        )
        return results

    # ── Internal Helpers ─────────────────────────────────────────────────

    async def _query(self, promql: str) -> list[dict[str, Any]]:
        """Execute an instant PromQL query and return the raw result list.

        Raises:
            PrometheusApiClientException: On query failure.
            ConnectionError: If Prometheus is unreachable.
        """
        logger.debug("PromQL query: %s", promql)
        try:
            results = await asyncio.to_thread(
                self._prom.custom_query, query=promql,
            )
            return results or []
        except PrometheusApiClientException:
            logger.exception("PromQL query failed: %s", promql)
            raise
        except Exception as exc:
            logger.exception("Prometheus connection error")
            raise ConnectionError(
                f"Cannot reach Prometheus at {self._url}"
            ) from exc

    async def _query_to_metrics(
        self,
        promql: str,
        namespace: str,
        metric_type: str,
    ) -> list[ContainerMetric]:
        """Run a PromQL query and parse results into ``ContainerMetric`` objects."""
        try:
            results = await self._query(promql)
        except Exception:
            logger.warning(
                "Returning empty metrics for %s/%s due to query failure",
                namespace, metric_type,
            )
            return []

        metrics: list[ContainerMetric] = []
        for r in results:
            m = r.get("metric", {})
            value_pair = r.get("value", [])
            if len(value_pair) < 2:
                continue

            try:
                value = float(value_pair[1])
            except (ValueError, TypeError):
                continue

            container = m.get("container", "")
            if not container or container == "POD":
                continue

            metrics.append(
                ContainerMetric(
                    namespace=m.get("namespace", namespace),
                    pod=m.get("pod", ""),
                    container=container,
                    value=value,
                )
            )

        return metrics
