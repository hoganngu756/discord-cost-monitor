"""
OpenCost API client for mapping resource deltas to financial metrics.

Uses ``aiohttp`` for non-blocking HTTP requests. Falls back to local
pricing profiles when the OpenCost API is unreachable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from config.settings import get_settings, PRICING_DIR

logger = logging.getLogger(__name__)


@dataclass
class CostAllocation:
    """Aggregated cost data for a namespace."""
    namespace: str
    cpu_cost: float
    memory_cost: float
    total_cost: float
    cpu_cores_avg: float
    memory_bytes_avg: float
    efficiency: float


@dataclass
class WorkloadCost:
    """Cost data for an individual workload within a namespace."""
    namespace: str
    workload_name: str
    workload_type: str
    cpu_cost: float
    memory_cost: float
    total_cost: float
    cpu_cores_requested: float
    cpu_cores_used: float
    memory_bytes_requested: float
    memory_bytes_used: float

    @property
    def cpu_efficiency(self) -> float:
        if self.cpu_cores_requested <= 0:
            return 100.0
        return min((self.cpu_cores_used / self.cpu_cores_requested) * 100, 100.0)

    @property
    def memory_efficiency(self) -> float:
        if self.memory_bytes_requested <= 0:
            return 100.0
        return min((self.memory_bytes_used / self.memory_bytes_requested) * 100, 100.0)

    @property
    def daily_waste(self) -> float:
        cpu_waste = self.cpu_cost * max(0, 1 - self.cpu_efficiency / 100)
        mem_waste = self.memory_cost * max(0, 1 - self.memory_efficiency / 100)
        return cpu_waste + mem_waste


class OpenCostClient:
    """Async client for the OpenCost allocation API with local pricing fallback."""

    def __init__(self, url: str | None = None) -> None:
        settings = get_settings()
        self._url = (url or settings.opencost_url).rstrip("/")
        self._pricing = self._load_pricing()
        self._session: aiohttp.ClientSession | None = None
        self._using_fallback = False
        logger.info("OpenCostClient initialized → %s", self._url)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15, connect=5)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    @property
    def is_using_fallback(self) -> bool:
        return self._using_fallback

    async def health_check(self) -> bool:
        try:
            session = await self._get_session()
            async with session.get(
                f"{self._url}/allocation/compute",
                params={"window": "1h", "aggregate": "namespace"},
            ) as resp:
                return resp.status == 200
        except Exception:
            logger.warning("OpenCost health check failed")
            return False

    async def get_namespace_costs(self, window: str | None = None) -> dict[str, CostAllocation]:
        window = window or get_settings().lookback_window
        data = await self._fetch_allocation(window=window, aggregate="namespace")
        if data is None:
            self._using_fallback = True
            return {}
        self._using_fallback = False
        return self._parse_namespace_costs(data)

    async def get_workload_costs(self, namespace: str, window: str | None = None) -> list[WorkloadCost]:
        window = window or get_settings().lookback_window
        data = await self._fetch_allocation(window=window, aggregate="controller", filter_namespaces=namespace)
        if data is None:
            self._using_fallback = True
            return []
        self._using_fallback = False
        return self._parse_workload_costs(data, namespace)

    def estimate_cost_from_delta(self, cpu_delta_cores: float, mem_delta_bytes: float, hours: float = 24.0) -> float:
        cpu_rate = self._pricing.get("cpu_cost_per_core_hour", 0.031611)
        mem_rate = self._pricing.get("memory_cost_per_gib_hour", 0.004237)
        mem_delta_gib = max(0, mem_delta_bytes) / (1024 ** 3)
        cpu_waste = max(0, cpu_delta_cores) * cpu_rate * hours
        mem_waste = mem_delta_gib * mem_rate * hours
        return round(cpu_waste + mem_waste, 6)

    def _load_pricing(self) -> dict[str, Any]:
        settings = get_settings()
        pricing_path = PRICING_DIR / settings.pricing_profile
        try:
            with open(pricing_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Could not load pricing profile — using built-in defaults")
            return {"cpu_cost_per_core_hour": 0.031611, "memory_cost_per_gib_hour": 0.004237}

    async def _fetch_allocation(self, window: str, aggregate: str, filter_namespaces: str | None = None) -> list[dict[str, Any]] | None:
        params: dict[str, str] = {"window": window, "aggregate": aggregate}
        if filter_namespaces:
            params["filterNamespaces"] = filter_namespaces
        try:
            session = await self._get_session()
            async with session.get(f"{self._url}/allocation/compute", params=params) as resp:
                if resp.status != 200:
                    logger.warning("OpenCost returned HTTP %d", resp.status)
                    return None
                payload = await resp.json()
                return payload.get("data", [])
        except aiohttp.ClientError:
            logger.warning("OpenCost API unreachable — using local pricing fallback")
            return None
        except Exception:
            logger.exception("Unexpected error querying OpenCost")
            return None

    @staticmethod
    def _parse_namespace_costs(data: list[dict[str, Any]]) -> dict[str, CostAllocation]:
        results: dict[str, CostAllocation] = {}
        for window_data in data:
            if not isinstance(window_data, dict):
                continue
            for ns_name, alloc in window_data.items():
                if not isinstance(alloc, dict) or ns_name == "__idle__":
                    continue
                cpu_cost = alloc.get("cpuCost", 0.0)
                ram_cost = alloc.get("ramCost", 0.0)
                cpu_eff = alloc.get("cpuEfficiency", 0.0)
                ram_eff = alloc.get("ramEfficiency", 0.0)
                avg_eff = ((cpu_eff + ram_eff) / 2) * 100 if (cpu_eff + ram_eff) else 0.0
                results[ns_name] = CostAllocation(
                    namespace=ns_name, cpu_cost=cpu_cost, memory_cost=ram_cost,
                    total_cost=cpu_cost + ram_cost, cpu_cores_avg=alloc.get("cpuCores", 0.0),
                    memory_bytes_avg=alloc.get("ramBytes", 0.0), efficiency=avg_eff,
                )
        return results

    @staticmethod
    def _parse_workload_costs(data: list[dict[str, Any]], namespace: str) -> list[WorkloadCost]:
        results: list[WorkloadCost] = []
        for window_data in data:
            if not isinstance(window_data, dict):
                continue
            for controller_key, alloc in window_data.items():
                if not isinstance(alloc, dict) or controller_key == "__idle__":
                    continue
                workload_name = controller_key
                workload_type = "unknown"
                if "/" in controller_key:
                    _, remainder = controller_key.split("/", 1)
                    if ":" in remainder:
                        workload_type, workload_name = remainder.split(":", 1)
                    else:
                        workload_name = remainder
                props = alloc.get("properties", {})
                wc = WorkloadCost(
                    namespace=namespace, workload_name=workload_name,
                    workload_type=workload_type.lower(),
                    cpu_cost=alloc.get("cpuCost", 0.0),
                    memory_cost=alloc.get("ramCost", 0.0),
                    total_cost=alloc.get("cpuCost", 0.0) + alloc.get("ramCost", 0.0),
                    cpu_cores_requested=props.get("cpuCoreRequestAverage", 0.0),
                    cpu_cores_used=props.get("cpuCoreUsageAverage", 0.0),
                    memory_bytes_requested=props.get("ramByteRequestAverage", 0.0),
                    memory_bytes_used=props.get("ramByteUsageAverage", 0.0),
                )
                results.append(wc)
        results.sort(key=lambda w: w.total_cost, reverse=True)
        return results
