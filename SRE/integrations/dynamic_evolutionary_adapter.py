# -*- coding: utf-8 -*-
"""Dynamic Evolutionary System adapter for Ops Factory.

https://github.com/ningshi01/Dynamic-Evolutionary-System

A cloud-native system dynamic evolution framework with adaptive RAG,
supporting microservice replica adjustment, service topology adjustment,
and data flow optimization.
"""

from __future__ import annotations

import json
import random
import subprocess
import time
from typing import Any, Dict, List, Optional

from .base_data_source import BaseDataSource


class DynamicEvolutionarySystemAdapter:
    """Adapter for the Dynamic Evolutionary System tool.

    Provides system evolution capabilities:
    1. Microservice replica adjustment (scaling)
    2. Service topology adjustment
    3. Data flow optimization

    Used by SRE as an analysis + remediation tool.
    """

    TOOL_NAME = "DynamicEvolutionarySystem"
    TOOL_DESCRIPTION = (
        "云原生软件系统动态演化框架，支持微服务副本调整、服务拓扑调整、数据流优化3种系统演化能力。"
        "将各RAG组件微服务化，实现基础设施资源的智能调度与分配。"
    )

    def __init__(self, data_source: BaseDataSource):
        self.data_source = data_source

    def analyze(self, case_id: str) -> Dict[str, Any]:
        """Analyze a fault case and suggest evolution actions.

        Returns evolution recommendations based on the fault evidence.
        """
        detail = self.data_source.get_case_detail(case_id)
        service_graph = detail.get("service_graph", {})
        metrics = detail.get("metrics", {})
        k8s_states = detail.get("k8s_states", {})

        evolution_actions = self._compute_evolution_actions(detail, service_graph, metrics, k8s_states)

        return {
            "tool": self.TOOL_NAME,
            "case_id": case_id,
            "source": detail.get("source", self.data_source.name),
            "analysis_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "evolution_actions": evolution_actions,
            "summary": (
                f"DynamicEvolutionarySystem 对来自 {self.data_source.name} 的故障案例进行了分析，"
                f"生成了 {len(evolution_actions)} 条系统演化建议。"
            ),
        }

    def _compute_evolution_actions(
        self, detail: Dict, service_graph: Dict, metrics: Dict, k8s_states: Dict
    ) -> List[Dict[str, Any]]:
        """Compute evolution actions based on evidence."""
        actions = []

        # Check for resource exhaustion → suggest replica scaling
        series_summary = metrics.get("series_summary", [])
        for item in sorted(series_summary, key=lambda x: x.get("range", 0), reverse=True)[:5]:
            service = item.get("service", "unknown")
            column = item.get("column", "")
            range_val = item.get("range", 0)

            if "cpu" in column.lower() and range_val > 0.3:
                actions.append({
                    "action_type": "replica_adjustment",
                    "target_service": service,
                    "recommendation": f"增加 {service} 的副本数以缓解 CPU 压力",
                    "current_replicas": detail.get("deployment_info", {}).get("replicas", {}).get(service, 1),
                    "recommended_replicas": 3,
                    "reason": f"CPU 波动范围 {range_val:.2f}，超出正常阈值",
                    "confidence": min(0.95, 0.5 + range_val),
                })
            elif "memory" in column.lower() and range_val > 100e6:
                actions.append({
                    "action_type": "replica_adjustment",
                    "target_service": service,
                    "recommendation": f"调整 {service} 的内存限制并考虑增加副本",
                    "reason": f"内存波动范围 {range_val:.0f} bytes",
                    "confidence": 0.8,
                })

        # Check K8s states for crash loops → suggest topology adjustment
        previews = k8s_states.get("previews", [])
        for preview in previews:
            text = preview.get("preview", "")
            if "CrashLoopBackOff" in text or "OOMKilled" in text:
                cmd = preview.get("command", "")
                actions.append({
                    "action_type": "topology_adjustment",
                    "recommendation": f"重新配置故障服务的部署拓扑 — 检测到 {cmd}",
                    "detail": text[:200],
                    "confidence": 0.9,
                })

        # Check service graph for bottleneck services
        edges = service_graph.get("edges", [])
        incoming_count = {}
        for edge in edges:
            target = edge.get("target", "")
            incoming_count[target] = incoming_count.get(target, 0) + 1

        for svc, count in sorted(incoming_count.items(), key=lambda x: x[1], reverse=True)[:3]:
            if count >= 3:
                actions.append({
                    "action_type": "data_flow_optimization",
                    "target_service": svc,
                    "recommendation": f"优化 {svc} 的数据流 — 该服务被 {count} 个上游服务调用，是系统瓶颈",
                    "incoming_edges": count,
                    "confidence": 0.7,
                })

        return actions[:8]

    def apply_evolution(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Apply an evolution action (simulated or real kubectl)."""
        action_type = action.get("action_type", "")
        target = action.get("target_service", "")

        if action_type == "replica_adjustment":
            replicas = action.get("recommended_replicas", 1)
            try:
                subprocess.run(
                    ["kubectl", "scale", "deployment", target, f"--replicas={replicas}"],
                    capture_output=True, timeout=30,
                )
                return {"status": "applied", "message": f"Scaled {target} to {replicas} replicas"}
            except Exception as e:
                return {"status": "simulated", "message": f"Would scale {target} to {replicas}: {e}"}
        return {"status": "simulated", "message": f"Evolution action simulated: {action}"}
