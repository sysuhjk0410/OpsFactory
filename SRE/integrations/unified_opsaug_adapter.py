# -*- coding: utf-8 -*-
"""Unified OpsAug adapter — supports all 4 data sources through BaseDataSource."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List

from .base_data_source import BaseDataSource


class UnifiedOpsAugAdapter:
    """Five-modality evidence summarizer that works with ANY data source.

    Instead of hard-coding CloudOpsBenchAdapter, this version accepts any
    BaseDataSource implementation, so it can process evidence from:
      - Cloud-OpsBench (static snapshots)
      - Online-Shop (GCP microservices demo)
      - Sock-Shop (microservices-demo)
      - Train-Ticket (serverless-trainticket)
    """

    def __init__(self, data_source: BaseDataSource):
        self.data_source = data_source

    def summarize_case(self, case_id: str) -> Dict[str, Any]:
        """Get case detail from the data source and produce a unified five-modality summary."""
        detail = self.data_source.get_case_detail(case_id)

        # Normalise the field names — dynamic sources and Cloud-OpsBench
        # use slightly different keys, so we provide a unified view.
        metrics_payload = self._normalise_metrics(detail.get("metrics", {}))
        logs_payload = self._normalise_logs(detail.get("logs", {}))
        alerts_payload = self._normalise_alerts(detail.get("alerts", {}))
        k8s_payload = self._normalise_k8s(detail.get("k8s_states", {}))
        trace_payload = self._normalise_service_graph(detail.get("service_graph", {}))

        log_summary = self._summarize_logs(logs_payload)
        metric_summary = self._summarize_metrics(metrics_payload)
        alert_summary = self._summarize_alerts(alerts_payload)
        k8s_summary = self._summarize_k8s(k8s_payload)
        trace_summary = self._summarize_service_graph(trace_payload)

        # Collect root cause candidates from all modalities
        candidates = []
        for group in [alert_summary["hints"], log_summary["hints"],
                      metric_summary["hints"], k8s_summary["hints"]]:
            candidates.extend(group)

        return {
            "case_id": case_id,
            "case_name": detail.get("case_name", case_id),
            "source": detail.get("source", self.data_source.name),
            "source_type": detail.get("source_type", self.data_source.source_type),
            "severity": detail.get("severity", "unknown"),
            "ground_truth": detail.get("root_cause_ground_truth"),
            "mode": "unified_multimodal_fusion",
            "modalities": {
                "metrics": metric_summary,
                "logs": log_summary,
                "alerts": alert_summary,
                "k8s_states": k8s_summary,
                "service_dependencies": trace_summary,
            },
            "root_cause_candidates": candidates[:10],
            "fusion_summary": (
                f"OpsAug 已读取来自 {self.data_source.name}（{self.data_source.source_type}）的故障数据，"
                f"将指标、日志、告警、K8s 状态和服务依赖五种模态统一汇总，"
                "供 SRE 和运维大模型继续做预警、定位与诊断。"
            ),
        }

    # ── normalisation helpers ────────────────────────────────────────
    def _normalise_metrics(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure metrics have series_summary key."""
        if "series_summary" in payload:
            return payload
        # Cloud-OpsBench format: has "series_summary" nested differently
        return {"series_summary": payload.get("series_summary", [])}

    def _normalise_logs(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure logs have entries key."""
        if "entries" in payload:
            return payload
        return {"entries": []}

    def _normalise_alerts(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise alerts to have alerts list and alert_count."""
        if isinstance(payload, dict) and "alerts" in payload:
            return payload
        if isinstance(payload, dict):
            alerts = payload.get("alerts", [])
            return {"alerts": alerts, "alert_count": payload.get("alert_count", len(alerts))}
        return {"alerts": [], "alert_count": 0}

    def _normalise_k8s(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise K8s states."""
        if "previews" in payload:
            return payload
        return {"previews": []}

    def _normalise_service_graph(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise service graph."""
        if "edges" in payload:
            return payload
        return {"services": [], "edges": []}

    # ── summarisation (same logic as before, but on normalised data) ─
    def _summarize_logs(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        entries = payload.get("entries", [])
        counter = Counter(
            (entry.get("service"), str(entry.get("level", "")).lower())
            for entry in entries
        )
        services = defaultdict(lambda: {"error": 0, "warn": 0, "info": 0})
        for (service, level), count in counter.items():
            if not service:
                continue
            services[service][level] = count

        ranked = sorted(
            (
                {
                    "service": service,
                    "error": counts.get("error", 0),
                    "warn": counts.get("warn", 0),
                    "info": counts.get("info", 0),
                }
                for service, counts in services.items()
            ),
            key=lambda item: (item["error"], item["warn"]),
            reverse=True,
        )

        hints = [
            f"日志模态显示 {item['service']} 出现 error={item['error']} warn={item['warn']}"
            for item in ranked[:5]
            if item["error"] or item["warn"]
        ]

        return {
            "service_levels": ranked[:10],
            "sample_messages": entries[:20],
            "hints": hints,
        }

    def _summarize_metrics(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        summary = payload.get("series_summary", [])
        ranked = sorted(summary, key=lambda item: item.get("range", 0), reverse=True)
        hints = [
            f"指标模态显示 {item['column']} 波动范围={item['range']}"
            for item in ranked[:5]
        ]
        return {"top_fluctuations": ranked[:10], "hints": hints}

    def _summarize_alerts(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        alerts = payload.get("alerts", []) if isinstance(payload, dict) else []
        hints = []
        for alert in alerts[:5]:
            message = alert.get("message") or alert.get("name") or str(alert)
            hints.append(f"告警模态提示: {message}")
        if not hints and payload:
            hints.append(payload.get("message", "该案例没有结构化告警，但存在异常摘要。"))
        return {
            "count": payload.get("alert_count", len(alerts)) if isinstance(payload, dict) else len(alerts),
            "alerts": alerts[:20],
            "hints": hints,
        }

    def _summarize_k8s(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        previews = payload.get("previews", [])
        keywords = ("CrashLoopBackOff", "OOMKilled", "ErrImagePull",
                    "ImagePullBackOff", "Pending", "Failed", "Error",
                    "RevisionMissing")
        hits = []
        for item in previews:
            preview = item.get("preview", "")
            matched = [keyword for keyword in keywords if keyword in preview]
            if matched:
                hits.append({"command": item.get("command"), "keywords": matched})
        hints = [
            f"K8s 状态模态在 `{item['command']}` 中发现 {', '.join(item['keywords'])}"
            for item in hits[:5]
        ]
        return {
            "commands": previews[:20],
            "hits": hits[:10],
            "hints": hints,
        }

    def _summarize_service_graph(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        edges = payload.get("edges", [])
        return {
            "service_count": len(payload.get("services", [])),
            "edge_count": len(edges),
            "edges": edges[:40],
            "hints": [f"服务依赖模态识别到 {len(edges)} 条调用边。"] if edges else [],
        }
