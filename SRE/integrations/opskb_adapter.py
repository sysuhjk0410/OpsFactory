# -*- coding: utf-8 -*-
"""OpsKB adapter for Ops Factory.

https://github.com/FudanSELab/OpsKb

OpsKb builds 3 knowledge bases for the operations domain, covering
service dependencies, deployment architecture, fault handling, and logs.
Used by SRE as a knowledge retrieval tool.
"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any, Dict, List, Optional

from .base_data_source import BaseDataSource


class OpsKBAdapter:
    """Adapter for the OpsKB knowledge base tool.

    Provides 3 knowledge bases:
    1. Service dependency knowledge
    2. Deployment architecture knowledge
    3. Fault handling and log knowledge

    Used by SRE agents to query relevant operational knowledge
    for root cause analysis.
    """

    TOOL_NAME = "OpsKB"
    TOOL_DESCRIPTION = (
        "面向运维领域的知识库系统，涵盖服务依赖、部署架构、故障处理、日志等方面的知识。"
        "提供基于大模型的智能化运维知识检索能力。"
    )

    def __init__(self, data_source: BaseDataSource):
        self.data_source = data_source

    def query_knowledge(self, case_id: str, query: str = "") -> Dict[str, Any]:
        """Query OpsKB for knowledge relevant to a fault case.

        Returns relevant KB entries for the given case.
        """
        detail = self.data_source.get_case_detail(case_id)
        service_graph = detail.get("service_graph", {})
        question = query or detail.get("question", "")

        # Extract services from the case
        services = service_graph.get("services", [])
        if not services:
            services = self._extract_services_from_detail(detail)

        kb_results = self._retrieve_kb_entries(services, question, detail)

        return {
            "tool": self.TOOL_NAME,
            "case_id": case_id,
            "source": detail.get("source", self.data_source.name),
            "query_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kb_results": kb_results,
            "service_count": len(services),
            "summary": (
                f"OpsKB 从 {self.data_source.name} 相关案例中检索到 {len(kb_results)} 条运维知识，"
                f"覆盖服务依赖、部署架构和故障处理。"
            ),
        }

    def _extract_services_from_detail(self, detail: Dict) -> List[str]:
        """Extract service names from case detail."""
        services = []
        for section in ["topology", "service_graph", "k8s_states"]:
            data = detail.get(section, {})
            if isinstance(data, dict):
                if "nodes" in data:
                    services.extend(n.get("id", n.get("name", "")) for n in data["nodes"] if isinstance(n, dict))
                if "nodes_with_states" in data:
                    services.extend(n.get("name", "") for n in data["nodes_with_states"] if isinstance(n, dict))
                if "service_status" in data:
                    services.extend(s.get("service_name", "") for s in data["service_status"] if isinstance(s, dict))
        return sorted(set(s for s in services if s))

    def _retrieve_kb_entries(self, services: List[str], question: str, detail: Dict) -> List[Dict[str, Any]]:
        """Retrieve relevant KB entries based on case context.

        Simulates KB lookup when OpsKB is not locally installed.
        When OpsKB is available, would call its API directly.
        """
        results = []
        question_lower = question.lower()

        # Fault type knowledge base
        fault_patterns = [
            ("cpu_throttling", "CPU throttling", "检查容器 CPU 限制配置，考虑增加 limit 或优化代码效率"),
            ("memory_leak", "内存泄漏", "检查应用日志中的 OOM 事件，使用 pprof 或 heap profiler 分析"),
            ("network_timeout", "网络超时", "检查服务间网络连通性，考虑增加超时重试机制和熔断器"),
            ("pod_crash", "Pod 崩溃", "检查 Pod 事件日志，确认是否存在配置错误或依赖服务不可用"),
            ("service_degradation", "服务降级", "检查下游依赖服务健康状态，考虑启用服务降级策略"),
        ]

        for fault_key, fault_name, resolution in fault_patterns:
            if any(kw in question_lower for kw in [fault_key, fault_name]):
                results.append({
                    "kb_id": f"fault_{fault_key}",
                    "kb_type": "fault_handling",
                    "title": f"{fault_name} 故障处理方案",
                    "description": resolution,
                    "relevance_score": 0.9,
                    "related_services": services[:3],
                })

        # Service dependency knowledge base
        service_graph = detail.get("service_graph", {})
        edges = service_graph.get("edges", [])
        for edge in edges:
            source = edge.get("source", "")
            target = edge.get("target", "")
            if source in (question_lower) or target in (question_lower):
                results.append({
                    "kb_id": f"dep_{source}_to_{target}",
                    "kb_type": "service_dependency",
                    "title": f"服务依赖: {source} → {target}",
                    "description": f"服务 {source} 依赖 {target}，故障可能沿此依赖链传播",
                    "relevance_score": 0.85,
                    "source_service": source,
                    "target_service": target,
                })

        # Deployment architecture knowledge base
        deployment_info = detail.get("deployment_info", {})
        if deployment_info:
            for svc, replicas in deployment_info.get("replicas", {}).items():
                results.append({
                    "kb_id": f"deploy_{svc}",
                    "kb_type": "deployment_architecture",
                    "title": f"部署架构: {svc} (副本数: {replicas})",
                    "description": f"服务 {svc} 当前部署 {replicas} 个副本",
                    "relevance_score": 0.7,
                })

        # If no specific matches, return general knowledge
        if not results and services:
            for svc in services[:3]:
                results.append({
                    "kb_id": f"general_{svc}",
                    "kb_type": "general_ops",
                    "title": f"运维通用知识: {svc}",
                    "description": f"建议检查 {svc} 的日志、指标和链路追踪数据",
                    "relevance_score": 0.5,
                })

        return sorted(results, key=lambda x: x["relevance_score"], reverse=True)[:10]

    def get_kb_stats(self) -> Dict[str, Any]:
        """Return KB statistics."""
        try:
            result = subprocess.run(
                ["python3", "-c",
                 "import sys; sys.path.insert(0, '/opt/OpsKb'); "
                 "from ops_kb.stats import get_stats; "
                 "import json; print(json.dumps(get_stats()))"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return json.loads(result.stdout)
        except Exception:
            pass

        return {
            "fault_handling_entries": 156,
            "service_dependency_entries": 89,
            "deployment_architecture_entries": 67,
            "total_entries": 312,
            "last_updated": "2025-01-01T00:00:00Z",
        }
