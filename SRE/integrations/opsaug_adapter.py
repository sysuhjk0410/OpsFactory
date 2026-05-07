"""OpsAug-style multimodal evidence summarizer for Cloud-OpsBench snapshots."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .cloudopsbench_adapter import CloudOpsBenchAdapter


class OpsAugAdapter:
    """Build a five-modality summary that SRE can consume directly."""

    def __init__(self, cloud_adapter: "CloudOpsBenchAdapter"):
        self.cloud_adapter = cloud_adapter

    def summarize_case(self, case_ref: str) -> Dict[str, Any]:
        detail = self.cloud_adapter.get_case_detail(case_ref)

        log_summary = self._summarize_logs(detail.get("logs", {}))
        metric_summary = self._summarize_metrics(detail.get("metrics", {}))
        alert_summary = self._summarize_alerts(detail.get("alerts", {}))
        k8s_summary = self._summarize_k8s(detail.get("k8s_states", {}))
        trace_summary = self._summarize_service_graph(detail.get("service_graph", {}))

        candidates = []
        for group in [alert_summary["hints"], log_summary["hints"], metric_summary["hints"], k8s_summary["hints"]]:
            candidates.extend(group)

        return {
            "case_ref": case_ref,
            "mode": "snapshot_multimodal_fusion",
            "modalities": {
                "metrics": metric_summary,
                "logs": log_summary,
                "alerts": alert_summary,
                "k8s_states": k8s_summary,
                "service_dependencies": trace_summary,
            },
            "root_cause_candidates": candidates[:8],
            "fusion_summary": (
                "OpsAug 已改为读取 Cloud-OpsBench 数据平台快照，将指标、日志、告警、K8s 状态和服务依赖五种模态统一汇总，"
                "供 SRE 和运维大模型继续做预警、定位与诊断。"
            ),
        }

    def _summarize_logs(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        entries = payload.get("entries", [])
        counter = Counter((entry.get("service"), str(entry.get("level", "")).lower()) for entry in entries)
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
        return {
            "top_fluctuations": ranked[:10],
            "hints": hints,
        }

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
        keywords = ("CrashLoopBackOff", "OOMKilled", "ErrImagePull", "ImagePullBackOff", "Pending", "Failed")
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
