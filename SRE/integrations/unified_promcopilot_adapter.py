# -*- coding: utf-8 -*-
"""Unified PromCopilot adapter — supports all 4 data sources."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base_data_source import BaseDataSource


class UnifiedPromCopilotAdapter:
    """PromQL generation that works with ANY data source through BaseDataSource.

    Replaces the Cloud-OpsBench-specific PromCopilotAdapter with a version
    that extracts service/metric info from any unified case detail.
    """

    def __init__(self, data_source: BaseDataSource):
        self.data_source = data_source

    def generate_for_case(self, case_id: str, question: str) -> Dict[str, Any]:
        """Generate PromQL for a case from any data source."""
        detail = self.data_source.get_case_detail(case_id)

        # Unified: try multiple possible key names for metric columns
        metric_columns = (
            detail.get("metric_columns", [])
            or detail.get("metric_inventory", [])
            or self._extract_columns_from_metrics(detail.get("metrics", {}))
        )
        services = (
            detail.get("service_inventory", [])
            or detail.get("service_graph", {}).get("services", [])
        )

        selected = self._select_columns(metric_columns, question)
        promql = self._build_promql(question, selected, services)

        return {
            "case_id": case_id,
            "question": question,
            "source": detail.get("source", self.data_source.name),
            "selected_columns": selected,
            "available_metric_count": len(metric_columns),
            "services": services,
            "knowledge_graph": detail.get("service_graph", {}),
            "promql": promql,
            "explanation": (
                f"PromCopilot 已切换为基于 {self.data_source.name}（{self.data_source.source_type}）"
                "数据的轻量知识检索模式。"
                "这里优先结合案例中真实出现的服务和指标列来生成 PromQL。"
            ),
        }

    def _extract_columns_from_metrics(self, metrics: Dict[str, Any]) -> List[str]:
        """Extract metric column names from series_summary."""
        series = metrics.get("series_summary", [])
        return [s.get("column", "") for s in series if s.get("column")]

    def _select_columns(self, metric_columns: List[str], question: str) -> List[str]:
        text = str(question or "").lower()
        keyword_groups = [
            ("latency", ["latency", "延迟", "慢", "时延"], ["p90latency", "p50latency"]),
            ("traffic", ["qps", "rps", "吞吐", "流量", "请求"], ["rps"]),
            ("cpu", ["cpu", "负载"], ["cpu", "cpu_cfs"]),
            ("memory", ["memory", "mem", "内存"], ["mem", "memory_usage_bytes"]),
            ("network", ["network", "网络"], ["network_receive", "network_transmit",
                                               "network_receive_bytes", "network_transmit_bytes"]),
            ("availability", ["success", "错误率", "成功率"], ["success_rate"]),
            ("error", ["error", "错误", "fail"], ["error_rate"]),
        ]

        selected: List[str] = []
        for _, keywords, suffixes in keyword_groups:
            if any(keyword in text for keyword in keywords):
                selected.extend(
                    [column for column in metric_columns
                     if any(column.endswith(f"-{suffix}") or column.endswith(suffix)
                            for suffix in suffixes)]
                )

        if not selected:
            selected = metric_columns[:8]

        deduped = []
        seen = set()
        for column in selected:
            if column in seen:
                continue
            seen.add(column)
            deduped.append(column)
        return deduped[:10]

    def _build_promql(self, question: str, selected_columns: List[str],
                      services: List[str]) -> str:
        if not selected_columns:
            return "sum(up)"

        text = str(question or "").lower()
        target_service = self._pick_service(question, services)
        metric_suffix = selected_columns[0].rsplit("-", 1)[-1]

        if metric_suffix in {"p90latency", "p50latency"}:
            quantile = "0.90" if metric_suffix == "p90latency" else "0.50"
            svc_filter = f',service="{target_service}"' if target_service else ""
            return (
                f'histogram_quantile({quantile}, '
                f'sum(rate(http_request_duration_seconds_bucket{{namespace=~".*"{svc_filter}}}[5m])) by (le, service))'
            )
        if metric_suffix == "rps":
            svc_filter = f',service="{target_service}"' if target_service else ""
            return f'sum(rate(http_requests_total{{namespace=~".*"{svc_filter}}}[5m])) by (service)'
        if metric_suffix in {"cpu", "cpu_cfs"}:
            pod_filter = f',pod=~"{target_service}.*"' if target_service else ""
            return (
                f'sum(rate(container_cpu_usage_seconds_total{{container!="POD"{pod_filter}}}[5m])) '
                f'by (pod, namespace)'
            )
        if metric_suffix in {"mem", "memory_usage_bytes"}:
            pod_filter = f',pod=~"{target_service}.*"' if target_service else ""
            return f'sum(container_memory_working_set_bytes{{container!="POD"{pod_filter}}}) by (pod, namespace)'
        if "network" in metric_suffix.lower():
            pod_filter = f',pod=~"{target_service}.*"' if target_service else ""
            selector = f'container!="POD"{pod_filter}'
            return f'sum(rate(container_network_receive_bytes_total{{{selector}}}[5m])) by (pod)'
        if metric_suffix in {"success_rate", "error_rate"} or any(
                token in text for token in ["error", "成功率", "availability"]):
            svc_filter = f',service="{target_service}"' if target_service else ""
            filter_clean = svc_filter[1:] if svc_filter.startswith(",") else svc_filter
            return (
                f'sum(rate(http_requests_total{{status!~"5.."{svc_filter}}}[5m])) '
                f'/ sum(rate(http_requests_total{{{{{filter_clean}}}}}[5m]))'
            )
        return "sum(up)"

    def _pick_service(self, question: str, services: List[str]) -> Optional[str]:
        text = str(question or "").lower()
        for service in services:
            if service.lower() in text:
                return service
        return services[0] if services else None
