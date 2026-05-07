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
from typing import Any, Dict, List, Optional

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
            "otel": {
                "traces": "Optional OTLP/JSON payload with resourceSpans",
                "metrics": "Optional OTLP/JSON payload with resourceMetrics",
                "logs": "Optional OTLP/JSON payload with resourceLogs",
            },
        }

    def otel_schema(self) -> Dict[str, Any]:
        """Return the OTEL integration contract supported by the enterprise endpoint."""
        return {
            "format": "OpenTelemetry OTLP/JSON",
            "batch_endpoint": "POST /api/datasources/custom/otel/register_case",
            "signal_endpoints": {
                "traces": "POST /api/datasources/custom/otel/v1/traces?case_id=<case>",
                "metrics": "POST /api/datasources/custom/otel/v1/metrics?case_id=<case>",
                "logs": "POST /api/datasources/custom/otel/v1/logs?case_id=<case>",
            },
            "batch_payload": {
                "case_id": "optional stable case id",
                "case_name": "optional title shown in the data platform",
                "root_cause_ground_truth": "optional ground truth for evaluation",
                "otel": {
                    "traces": {"resourceSpans": []},
                    "metrics": {"resourceMetrics": []},
                    "logs": {"resourceLogs": []},
                },
                "service_graph": "optional; inferred from spans when omitted",
                "enterprise_metadata": "optional business metadata",
            },
            "notes": [
                "The endpoint accepts standard OTLP JSON shapes and normalizes them into Ops Factory cases.",
                "For separate signal endpoints, pass the same case_id query parameter to merge traces, metrics and logs into one case.",
                "OTLP protobuf bytes are not required by this dashboard endpoint; configure the upstream bridge to send OTLP JSON.",
            ],
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

    def register_otel_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Register a case from a standard OTLP/JSON batch payload."""
        converted = self._otel_to_case_payload(payload)
        result = self.register_case(converted)
        result["message"] = "OTEL payload registered and normalized into an RCA case."
        result["otel_stats"] = converted.get("enterprise_metadata", {}).get("otel_stats", {})
        return result

    def register_otel_signal(
        self,
        signal_type: str,
        signal_payload: Dict[str, Any],
        *,
        case_id: str = "",
        case_name: str = "",
        root_cause_ground_truth: str = "",
    ) -> Dict[str, Any]:
        """Register or merge one OTEL signal into a custom case.

        This mirrors OTLP HTTP paths so an internal OTEL bridge can send traces,
        metrics and logs separately while keeping them under one case_id.
        """
        signal_type = str(signal_type or "").lower().strip()
        if signal_type not in {"traces", "metrics", "logs"}:
            raise DataSourceError(f"Unsupported OTEL signal type: {signal_type}")

        stable_case_id = str(case_id or f"otel-{uuid.uuid4().hex[:10]}").strip()
        if not stable_case_id:
            raise DataSourceError("case_id cannot be empty")

        converted = self._otel_to_case_payload({
            "case_id": stable_case_id,
            "case_name": case_name or f"OTEL case {stable_case_id}",
            "root_cause_ground_truth": root_cause_ground_truth,
            "otel": {signal_type: signal_payload},
            "enterprise_metadata": {"origin_system": "opentelemetry"},
        })

        path = self._case_path(stable_case_id)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            converted = self._merge_cases(existing, converted)

        result = self.register_case(converted)
        result["message"] = f"OTEL {signal_type} signal accepted and merged into case."
        result["otel_stats"] = converted.get("enterprise_metadata", {}).get("otel_stats", {})
        return result

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

    def _otel_to_case_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise DataSourceError("OTEL payload must be a JSON object")

        otel = payload.get("otel") if isinstance(payload.get("otel"), dict) else {}
        traces_payload = self._pick_otel_signal(payload, otel, "traces", "resourceSpans")
        metrics_payload = self._pick_otel_signal(payload, otel, "metrics", "resourceMetrics")
        logs_payload = self._pick_otel_signal(payload, otel, "logs", "resourceLogs")

        spans = self._parse_otel_traces(traces_payload)
        raw_metrics, metric_summary = self._parse_otel_metrics(metrics_payload)
        log_entries = self._parse_otel_logs(logs_payload)

        graph = payload.get("service_graph") if isinstance(payload.get("service_graph"), dict) else {}
        services = set(graph.get("services") or payload.get("service_inventory") or [])
        services.update(s.get("service", "") for s in spans)
        services.update(m.get("service", "") for m in raw_metrics)
        services.update(l.get("service", "") for l in log_entries)
        services.discard("")

        inferred_edges = self._infer_edges_from_spans(spans)
        edges = list(graph.get("edges") or [])
        edge_keys = {(e.get("source"), e.get("target"), e.get("call_type", "")) for e in edges if isinstance(e, dict)}
        for edge in inferred_edges:
            key = (edge.get("source"), edge.get("target"), edge.get("call_type", ""))
            if key not in edge_keys:
                edges.append(edge)
                edge_keys.add(key)

        alerts = payload.get("alerts") if isinstance(payload.get("alerts"), dict) else {}
        generated_alerts = self._alerts_from_otel_logs(log_entries)
        alert_rows = list(alerts.get("alerts", []) or [])
        alert_rows.extend(generated_alerts)

        metric_columns = [row.get("column") for row in metric_summary if row.get("column")]
        metadata = dict(payload.get("enterprise_metadata") or {})
        metadata.setdefault("origin_system", "opentelemetry")
        metadata["otel_stats"] = {
            "span_count": len(spans),
            "metric_point_count": len(raw_metrics),
            "metric_series_count": len(metric_summary),
            "log_count": len(log_entries),
            "service_count": len(services),
        }

        case_id = str(payload.get("case_id") or f"otel-{uuid.uuid4().hex[:10]}").strip()
        return {
            "case_id": case_id,
            "case_name": payload.get("case_name") or payload.get("name") or f"OTEL case {case_id}",
            "severity": payload.get("severity") or self._severity_from_logs(log_entries),
            "fault_type": payload.get("fault_type", "otel_observability_case"),
            "description": payload.get("description") or "标准 OpenTelemetry 采集数据接入案例",
            "root_cause_ground_truth": payload.get("root_cause_ground_truth", ""),
            "metrics": {
                "series_summary": metric_summary,
                "raw_series": raw_metrics,
            },
            "logs": {"entries": log_entries},
            "traces": {"spans": spans},
            "alerts": {
                "alerts": alert_rows,
                "alert_count": len(alert_rows),
            },
            "service_graph": {
                "services": sorted(services),
                "edges": edges,
            },
            "metric_columns": metric_columns,
            "service_inventory": sorted(services),
            "enterprise_metadata": metadata,
        }

    def _pick_otel_signal(self, payload: Dict[str, Any], otel: Dict[str, Any], signal_name: str, root_key: str) -> Dict[str, Any]:
        candidate = otel.get(signal_name)
        if isinstance(candidate, dict) and root_key in candidate:
            return candidate
        if root_key in payload:
            return payload
        candidate = payload.get(signal_name)
        if isinstance(candidate, dict) and root_key in candidate:
            return candidate
        return {}

    def _parse_otel_traces(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        spans: List[Dict[str, Any]] = []
        for resource_span in payload.get("resourceSpans", []) or []:
            resource_attrs = self._otel_attrs(resource_span.get("resource", {}).get("attributes", []))
            service = self._otel_service_name(resource_attrs)
            scope_spans = resource_span.get("scopeSpans") or resource_span.get("instrumentationLibrarySpans") or []
            for scope_span in scope_spans:
                scope = scope_span.get("scope") or scope_span.get("instrumentationLibrary") or {}
                for span in scope_span.get("spans", []) or []:
                    attrs = self._otel_attrs(span.get("attributes", []))
                    start_ns = self._to_int(span.get("startTimeUnixNano"))
                    end_ns = self._to_int(span.get("endTimeUnixNano"))
                    duration_ms = round((end_ns - start_ns) / 1_000_000, 3) if end_ns and start_ns and end_ns >= start_ns else None
                    status = span.get("status") or {}
                    spans.append({
                        "trace_id": span.get("traceId", ""),
                        "span_id": span.get("spanId", ""),
                        "parent_span_id": span.get("parentSpanId", ""),
                        "service": attrs.get("service.name") or service,
                        "operation": span.get("name", ""),
                        "kind": span.get("kind", ""),
                        "start_time": self._otel_time(start_ns),
                        "end_time": self._otel_time(end_ns),
                        "duration_ms": duration_ms,
                        "status": status.get("message") or status.get("code", ""),
                        "attributes": attrs,
                        "scope": scope.get("name", ""),
                    })
        return spans

    def _parse_otel_metrics(self, payload: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        raw: List[Dict[str, Any]] = []
        grouped: Dict[str, List[float]] = {}
        for resource_metric in payload.get("resourceMetrics", []) or []:
            resource_attrs = self._otel_attrs(resource_metric.get("resource", {}).get("attributes", []))
            service = self._otel_service_name(resource_attrs)
            scope_metrics = resource_metric.get("scopeMetrics") or resource_metric.get("instrumentationLibraryMetrics") or []
            for scope_metric in scope_metrics:
                for metric in scope_metric.get("metrics", []) or []:
                    metric_name = metric.get("name", "otel_metric")
                    unit = metric.get("unit", "")
                    points = self._otel_metric_points(metric)
                    for point in points:
                        attrs = self._otel_attrs(point.get("attributes", []))
                        point_service = attrs.get("service.name") or attrs.get("service") or service
                        value = self._otel_metric_value(point)
                        if value is None:
                            continue
                        column = f"{point_service}-{metric_name}" if point_service else metric_name
                        raw_row = {
                            "timestamp": self._otel_time(self._to_int(point.get("timeUnixNano") or point.get("observedTimeUnixNano"))),
                            "service": point_service,
                            "metric": metric_name,
                            "column": column,
                            "value": value,
                            "unit": unit,
                            "attributes": attrs,
                        }
                        raw.append(raw_row)
                        grouped.setdefault(column, []).append(float(value))

        summary = []
        for column, values in grouped.items():
            if not values:
                continue
            service, metric = column.split("-", 1) if "-" in column else ("", column)
            mean = sum(values) / len(values)
            min_val = min(values)
            max_val = max(values)
            summary.append({
                "column": column,
                "service": service,
                "metric": metric,
                "mean": round(mean, 6),
                "min": round(min_val, 6),
                "max": round(max_val, 6),
                "range": round(max_val - min_val, 6),
                "std": 0.0,
                "point_count": len(values),
            })
        return raw, summary

    def _parse_otel_logs(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for resource_log in payload.get("resourceLogs", []) or []:
            resource_attrs = self._otel_attrs(resource_log.get("resource", {}).get("attributes", []))
            service = self._otel_service_name(resource_attrs)
            scope_logs = resource_log.get("scopeLogs") or resource_log.get("instrumentationLibraryLogs") or []
            for scope_log in scope_logs:
                for record in scope_log.get("logRecords", []) or []:
                    attrs = self._otel_attrs(record.get("attributes", []))
                    body = self._otel_value(record.get("body", {}))
                    entries.append({
                        "timestamp": self._otel_time(self._to_int(record.get("timeUnixNano") or record.get("observedTimeUnixNano"))),
                        "service": attrs.get("service.name") or attrs.get("service") or service,
                        "level": record.get("severityText") or self._severity_text(record.get("severityNumber")),
                        "message": str(body if body is not None else ""),
                        "attributes": attrs,
                        "trace_id": record.get("traceId", ""),
                        "span_id": record.get("spanId", ""),
                    })
        return entries

    def _otel_metric_points(self, metric: Dict[str, Any]) -> List[Dict[str, Any]]:
        for key in ("gauge", "sum", "histogram", "exponentialHistogram", "summary"):
            section = metric.get(key)
            if isinstance(section, dict):
                return list(section.get("dataPoints", []) or [])
        return []

    def _otel_metric_value(self, point: Dict[str, Any]) -> Optional[float]:
        for key in ("asDouble", "asInt", "doubleValue", "intValue", "value", "sum", "count"):
            if key in point:
                return self._to_float(point.get(key))
        if "quantileValues" in point and point["quantileValues"]:
            return self._to_float(point["quantileValues"][0].get("value"))
        return None

    def _otel_attrs(self, attrs: List[Dict[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for item in attrs or []:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if not key:
                continue
            result[key] = self._otel_value(item.get("value", {}))
        return result

    def _otel_value(self, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        for key in ("stringValue", "intValue", "doubleValue", "boolValue", "bytesValue"):
            if key in value:
                return value.get(key)
        if "arrayValue" in value:
            return [self._otel_value(v) for v in value.get("arrayValue", {}).get("values", [])]
        if "kvlistValue" in value:
            return self._otel_attrs(value.get("kvlistValue", {}).get("values", []))
        return None

    def _otel_service_name(self, resource_attrs: Dict[str, Any]) -> str:
        return str(
            resource_attrs.get("service.name")
            or resource_attrs.get("k8s.deployment.name")
            or resource_attrs.get("k8s.pod.name")
            or resource_attrs.get("service")
            or "unknown-service"
        )

    def _infer_edges_from_spans(self, spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_span = {s.get("span_id"): s for s in spans if s.get("span_id")}
        edges = []
        seen = set()
        for span in spans:
            parent = by_span.get(span.get("parent_span_id"))
            if not parent:
                continue
            source = parent.get("service")
            target = span.get("service")
            if not source or not target or source == target:
                continue
            key = (source, target)
            if key in seen:
                continue
            seen.add(key)
            edges.append({"source": source, "target": target, "call_type": "otel_span"})
        return edges

    def _alerts_from_otel_logs(self, logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        alerts = []
        for item in logs:
            level = str(item.get("level") or "").upper()
            if level in {"ERROR", "FATAL", "CRITICAL"} or self._severity_rank(level) >= 17:
                alerts.append({
                    "name": "OTELLogError",
                    "severity": "critical" if level in {"FATAL", "CRITICAL"} else "warning",
                    "service": item.get("service", ""),
                    "message": item.get("message", "")[:240],
                    "timestamp": item.get("timestamp", ""),
                })
        return alerts[:20]

    def _severity_from_logs(self, logs: List[Dict[str, Any]]) -> str:
        levels = {str(item.get("level") or "").upper() for item in logs}
        if levels & {"FATAL", "CRITICAL"}:
            return "critical"
        if levels & {"ERROR"}:
            return "warning"
        return "info"

    def _severity_text(self, value: Any) -> str:
        rank = self._severity_rank(value)
        if rank >= 21:
            return "FATAL"
        if rank >= 17:
            return "ERROR"
        if rank >= 13:
            return "WARN"
        if rank >= 9:
            return "INFO"
        if rank >= 5:
            return "DEBUG"
        return "TRACE"

    def _severity_rank(self, value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return {"TRACE": 1, "DEBUG": 5, "INFO": 9, "WARN": 13, "WARNING": 13, "ERROR": 17, "FATAL": 21, "CRITICAL": 21}.get(str(value or "").upper(), 0)

    def _otel_time(self, ns: int) -> str:
        if not ns:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ")
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ns / 1_000_000_000))

    @staticmethod
    def _to_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _merge_cases(self, existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(existing)
        merged["case_name"] = incoming.get("case_name") or existing.get("case_name")
        merged["root_cause_ground_truth"] = incoming.get("root_cause_ground_truth") or existing.get("root_cause_ground_truth", "")
        merged["description"] = incoming.get("description") or existing.get("description", "")
        merged["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")

        merged_metrics = dict(existing.get("metrics") or {})
        incoming_metrics = incoming.get("metrics") or {}
        merged_metrics["series_summary"] = self._dedupe_rows(
            list(merged_metrics.get("series_summary", []) or []) + list(incoming_metrics.get("series_summary", []) or []),
            ["column", "service", "metric"],
        )
        merged_metrics["raw_series"] = self._dedupe_rows(
            list(merged_metrics.get("raw_series", []) or []) + list(incoming_metrics.get("raw_series", []) or []),
            ["timestamp", "service", "metric", "value"],
        )
        merged["metrics"] = merged_metrics

        for section, key, dedupe_keys in [
            ("logs", "entries", ["timestamp", "service", "level", "message"]),
            ("traces", "spans", ["trace_id", "span_id"]),
            ("alerts", "alerts", ["name", "service", "message", "timestamp"]),
        ]:
            current = dict(existing.get(section) or {})
            current[key] = self._dedupe_rows(list(current.get(key, []) or []) + list((incoming.get(section) or {}).get(key, []) or []), dedupe_keys)
            if section == "alerts":
                current["alert_count"] = len(current[key])
            merged[section] = current

        graph = dict(existing.get("service_graph") or {})
        incoming_graph = incoming.get("service_graph") or {}
        services = sorted(set(graph.get("services", []) or []) | set(incoming_graph.get("services", []) or []))
        edges = self._dedupe_rows(list(graph.get("edges", []) or []) + list(incoming_graph.get("edges", []) or []), ["source", "target", "call_type"])
        merged["service_graph"] = {"services": services, "edges": edges}
        merged["service_inventory"] = services
        merged["metric_columns"] = sorted(set(existing.get("metric_columns", []) or []) | set(incoming.get("metric_columns", []) or []))

        metadata = dict(existing.get("enterprise_metadata") or {})
        metadata.update(incoming.get("enterprise_metadata") or {})
        metadata["otel_stats"] = {
            "span_count": len(merged.get("traces", {}).get("spans", []) or []),
            "metric_point_count": len(merged.get("metrics", {}).get("raw_series", []) or []),
            "metric_series_count": len(merged.get("metrics", {}).get("series_summary", []) or []),
            "log_count": len(merged.get("logs", {}).get("entries", []) or []),
            "service_count": len(services),
        }
        merged["enterprise_metadata"] = metadata
        return merged

    def _dedupe_rows(self, rows: List[Dict[str, Any]], keys: List[str]) -> List[Dict[str, Any]]:
        deduped = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = tuple(str(row.get(k, "")) for k in keys)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        return deduped
