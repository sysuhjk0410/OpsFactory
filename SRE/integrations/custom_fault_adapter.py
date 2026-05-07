# -*- coding: utf-8 -*-
"""Enterprise custom fault-data adapter.

This adapter gives the data platform a stable integration surface for internal
systems: users can register a fault case with logs, traces, metrics, alerts and
service topology, then run the same RCA pipeline used by built-in sources.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from .base_data_source import BaseDataSource, DataSourceError


DEFAULT_CUSTOM_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "custom_sources"


class CustomFaultAdapter(BaseDataSource):
    """File-backed adapter for enterprise platforms and ad-hoc fault cases."""

    def __init__(self, storage_dir: str | Path = DEFAULT_CUSTOM_DATA_DIR):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "Enterprise Custom Fault Data"

    @property
    def source_type(self) -> str:
        return "custom"

    @property
    def description(self) -> str:
        return "企业内部平台、自定义故障样本和外部工具输出的统一接入口"

    def schema(self) -> Dict[str, Any]:
        return {
            "case_id": "optional string, omitted means auto-generated",
            "case_name": "Human readable title",
            "root_cause_ground_truth": "Optional ground truth for evaluation",
            "metrics": {
                "series_summary": [
                    {"column": "service-metric", "service": "service", "mean": 0.1, "max": 1.0}
                ],
                "raw_series": [
                    {"timestamp": "2026-04-27T10:00:00Z", "service": "cart", "metric": "error_rate", "value": 0.3}
                ],
            },
            "logs": {
                "entries": [
                    {"timestamp": "2026-04-27T10:00:00Z", "service": "cart", "level": "ERROR", "message": "timeout"}
                ]
            },
            "traces": {
                "spans": [
                    {"trace_id": "t-1", "span_id": "s-1", "service": "front-end", "operation": "GET /cart", "duration_ms": 120}
                ]
            },
            "alerts": {
                "alerts": [
                    {"name": "HighErrorRate", "severity": "critical", "service": "cart", "message": "error_rate > 20%"}
                ]
            },
            "service_graph": {
                "services": ["front-end", "cart"],
                "edges": [{"source": "front-end", "target": "cart", "call_type": "http"}],
            },
            "enterprise_metadata": {
                "origin_system": "internal-observability-platform",
                "external_case_url": "https://internal.example/case/123",
            },
        }

    def register_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        case_id = str(payload.get("case_id") or f"custom-{uuid.uuid4().hex[:10]}").strip()
        if not case_id:
            raise DataSourceError("case_id cannot be empty")
        normalized = self._normalize_case(case_id, payload)
        with open(self._case_path(case_id), "w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)
        return {
            "status": "ok",
            "source_id": "custom-enterprise",
            "case_id": case_id,
            "message": "Custom fault case registered and ready for RCA.",
        }

    def list_faults(self) -> List[Dict[str, Any]]:
        cases: List[Dict[str, Any]] = []
        for path in sorted(self.storage_dir.glob("*.json"), reverse=True):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            cases.append({
                "case_id": data.get("case_id", path.stem),
                "fault_type": data.get("fault_type", "custom_fault"),
                "case_name": data.get("case_name", path.stem),
                "timestamp": data.get("timestamp", ""),
                "severity": data.get("severity", "warning"),
                "description": data.get("description", self.description),
            })
        return cases

    def inject_fault(self, fault_type: str, target: str, **kwargs) -> Dict[str, Any]:
        """Create a synthetic custom fault case when no external payload exists."""
        payload = {
            "fault_type": fault_type,
            "case_name": f"{fault_type} on {target}",
            "severity": kwargs.get("severity", "critical"),
            "root_cause_ground_truth": f"{target} is the root cause.",
            "service_graph": kwargs.get("service_graph") or {
                "services": [target, "upstream-gateway", "downstream-db"],
                "edges": [
                    {"source": "upstream-gateway", "target": target, "call_type": "http"},
                    {"source": target, "target": "downstream-db", "call_type": "tcp"},
                ],
            },
            "metrics": kwargs.get("metrics") or {
                "series_summary": [
                    {"column": f"{target}-error_rate", "service": target, "mean": 0.41, "std": 0.12, "min": 0.02, "max": 0.93, "range": 0.91},
                    {"column": f"{target}-latency_p99", "service": target, "mean": 2.4, "std": 0.5, "min": 0.12, "max": 6.8, "range": 6.68},
                ]
            },
            "logs": kwargs.get("logs") or {
                "entries": [
                    {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "service": target, "level": "ERROR", "message": f"{target} {fault_type} injected by custom adapter"},
                    {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "service": "upstream-gateway", "level": "WARN", "message": f"downstream {target} timeout after 3000ms"},
                ]
            },
            "traces": kwargs.get("traces") or {
                "spans": [
                    {"trace_id": "custom-trace-1", "span_id": "s1", "service": "upstream-gateway", "operation": "GET /api", "duration_ms": 3100},
                    {"trace_id": "custom-trace-1", "span_id": "s2", "parent_span_id": "s1", "service": target, "operation": "handle", "duration_ms": 2980},
                ]
            },
            "alerts": kwargs.get("alerts") or {
                "alerts": [
                    {"name": fault_type, "severity": "critical", "service": target, "message": f"Injected {fault_type}"}
                ],
                "alert_count": 1,
            },
            "enterprise_metadata": kwargs.get("enterprise_metadata", {}),
        }
        return self.register_case(payload)

    def get_case_detail(self, case_id: str) -> Dict[str, Any]:
        path = self._case_path(case_id)
        if not path.exists():
            raise DataSourceError(f"Custom case not found: {case_id}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def health_check(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "message": f"{len(self.list_faults())} custom cases registered",
            "storage_dir": str(self.storage_dir),
        }

    def _case_path(self, case_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in case_id)
        return self.storage_dir / f"{safe}.json"

    def _normalize_case(self, case_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        graph = payload.get("service_graph") or {}
        services = graph.get("services") or payload.get("service_inventory") or []
        metrics = payload.get("metrics") or {}
        logs = payload.get("logs") or {}
        alerts = payload.get("alerts") or {}
        return {
            "case_id": case_id,
            "case_name": payload.get("case_name") or payload.get("name") or case_id,
            "source": self.name,
            "source_type": self.source_type,
            "timestamp": payload.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "severity": payload.get("severity", "warning"),
            "fault_type": payload.get("fault_type", "custom_fault"),
            "description": payload.get("description", ""),
            "root_cause_ground_truth": payload.get("root_cause_ground_truth", ""),
            "metrics": {
                "series_summary": self._normalize_metric_summary(metrics.get("series_summary", [])),
                "raw_series": metrics.get("raw_series", []),
            },
            "logs": {"entries": logs.get("entries", [])},
            "traces": payload.get("traces", {"spans": []}),
            "alerts": {
                "alerts": alerts.get("alerts", []),
                "alert_count": alerts.get("alert_count", len(alerts.get("alerts", []))),
            },
            "k8s_states": payload.get("k8s_states", {"previews": []}),
            "service_graph": {
                "services": services,
                "edges": graph.get("edges", []),
            },
            "metric_columns": payload.get("metric_columns") or [
                item.get("column") for item in metrics.get("series_summary", []) if item.get("column")
            ],
            "service_inventory": services,
            "deployment_info": payload.get("deployment_info", {}),
            "enterprise_metadata": payload.get("enterprise_metadata", {}),
        }

    def _normalize_metric_summary(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            mean = self._to_float(item.get("mean", item.get("value", 0)))
            max_val = self._to_float(item.get("max", mean))
            min_val = self._to_float(item.get("min", mean))
            item.setdefault("mean", mean)
            item.setdefault("std", abs(max_val - min_val) / 6 if max_val != min_val else 0.0)
            item.setdefault("min", min_val)
            item.setdefault("max", max_val)
            item.setdefault("range", max_val - min_val)
            normalized.append(item)
        return normalized

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0
