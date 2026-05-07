"""Unified data source interface for Ops Factory — all data sources must implement this."""

from __future__ import annotations

import abc
from typing import Any, Dict, List, Optional


class DataSourceError(Exception):
    """Raised when a data source cannot fetch or process data."""


class BaseDataSource(abc.ABC):
    """Abstract interface that every data source (static or dynamic) must implement.

    This ensures OpsAug, PromCopilot, and the SRE orchestrator can
    consume data from *any* source through a single unified schema.
    """

    # ── metadata (override in subclass) ──────────────────────────────
    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable source name, e.g. 'Cloud-OpsBench', 'Online-Shop'."""

    @property
    @abc.abstractmethod
    def source_type(self) -> str:
        """'static' for Cloud-OpsBench, 'dynamic' for the three live platforms."""

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """Short description shown in the UI data-source selector."""

    # ── fault injection / case management ────────────────────────────
    @abc.abstractmethod
    def list_faults(self) -> List[Dict[str, Any]]:
        """Return a list of available fault cases / injection scenarios.

        Each dict must contain at least:
            - case_id: unique identifier
            - case_name: short description
            - timestamp: when the fault was detected / injected
            - severity: 'critical' | 'warning' | 'info'
        """

    @abc.abstractmethod
    def inject_fault(self, fault_type: str, target: str, **kwargs) -> Dict[str, Any]:
        """Inject a fault into the live platform (dynamic sources only).

        Dynamic adapters should accept the following timing kwargs:
            - scheduled_at / start_time / injection_time
            - duration_seconds
            - observation_window_seconds
            - pre_window_seconds
            - collection_interval_seconds
            - injection_mode

        For static sources this may raise NotImplementedError or be a no-op.
        Returns a dict with at least {case_id, status, message, fault_injection}.
        """

    # ── unified evidence schema (the core output) ────────────────────
    @abc.abstractmethod
    def get_case_detail(self, case_id: str) -> Dict[str, Any]:
        """Fetch full evidence for a single case.

        The returned dict MUST follow this unified schema so that all
        downstream tools (OpsAug, PromCopilot, DrainMCP, etc.) can work
        with *any* data source without modification:

        {
            "case_id": str,
            "case_name": str,
            "source": str,           # self.name
            "source_type": str,      # self.source_type
            "timestamp": str,
            "severity": str,
            "root_cause_ground_truth": str | None,  # for evaluation

            # ── five-modalities evidence ─────────────────────────────
            "metrics": {
                "series_summary": [          # pre-aggregated metric stats
                    {"column": str, "service": str, "mean": float,
                     "std": float, "min": float, "max": float, "range": float},
                ],
                "raw_series": [              # optional raw time-series
                    {"timestamp": str, "service": str, "metric": str, "value": float},
                ],
            },
            "logs": {
                "entries": [
                    {"timestamp": str, "service": str, "level": str, "message": str},
                ],
            },
            "alerts": {
                "alerts": [
                    {"name": str, "severity": str, "message": str,
                     "service": str, "timestamp": str},
                ],
                "alert_count": int,
            },
            "k8s_states": {
                "previews": [
                    {"command": str, "resource": str, "preview": str},
                ],
            },
            "service_graph": {
                "services": [str],
                "edges": [
                    {"source": str, "target": str, "call_type": str},
                ],
            },

            # ── optional extra fields ───────────────────────────────
            "metric_columns": [str],     # column names for PromCopilot
            "service_inventory": [str],
            "deployment_info": {         # deployment architecture
                "namespaces": [str],
                "replicas": {str: int},
            },
        }
        """

    # ── real-time monitoring (dynamic sources) ──────────────────────
    def get_live_metrics(self, service: Optional[str] = None) -> Dict[str, Any]:
        """Fetch current live metrics from the platform.

        Default implementation returns empty — override in dynamic sources.
        """
        return {"services": [], "metrics": []}

    def get_live_logs(self, service: Optional[str] = None,
                      lines: int = 100) -> List[Dict[str, Any]]:
        """Fetch recent log lines from the platform.

        Default implementation returns empty — override in dynamic sources.
        """
        return []

    def health_check(self) -> Dict[str, Any]:
        """Check if the data source is reachable and healthy."""
        return {"status": "unknown", "message": "Not implemented"}

    def restore_fault(self, case_id: str = "", target: str = "", fault_type: str = "") -> Dict[str, Any]:
        """Restore a dynamic fault injection if the source supports it."""
        raise NotImplementedError("Fault restoration is not supported by this data source.")
