"""PromCopilot-style PromQL generation backed by Cloud-OpsBench case data."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .cloudopsbench_adapter import CloudOpsBenchAdapter


class PromCopilotAdapter:
    """Generate case-aware PromQL suggestions without external databases."""

    def __init__(self, cloud_adapter: "CloudOpsBenchAdapter"):
        self.cloud_adapter = cloud_adapter

    def generate_for_case(self, case_ref: str, question: str) -> Dict[str, Any]:
        detail = self.cloud_adapter.get_case_detail(case_ref)
        metric_columns = detail.get("metric_columns", [])
        services = detail.get("service_inventory", [])
        selected = self._select_columns(metric_columns, question)

        promql = self._build_promql(question, selected, services)
        return {
            "case_ref": case_ref,
            "question": question,
            "selected_columns": selected,
            "available_metric_count": len(metric_columns),
            "services": services,
            "knowledge_graph": detail.get("service_graph", {}),
            "promql": promql,
            "explanation": (
                "PromCopilot 已切换为基于 Cloud-OpsBench 快照数据的轻量知识检索模式。"
                "这里优先结合案例中真实出现的服务和指标列来生成 PromQL。"
            ),
        }

    def _select_columns(self, metric_columns: List[str], question: str) -> List[str]:
        text = str(question or "").lower()
        keyword_groups = [
            ("latency", ["latency", "延迟", "慢", "时延"], ["p90latency", "p50latency"]),
            ("traffic", ["qps", "rps", "吞吐", "流量", "请求"], ["rps"]),
            ("cpu", ["cpu", "负载"], ["cpu", "cpu_cfs"]),
            ("memory", ["memory", "mem", "内存"], ["mem"]),
            ("network", ["network", "网络"], ["network_receive", "network_transmit"]),
            ("availability", ["success", "错误率", "成功率"], ["success_rate"]),
        ]

        selected: List[str] = []
        for _, keywords, suffixes in keyword_groups:
            if any(keyword in text for keyword in keywords):
                selected.extend(
                    [column for column in metric_columns if any(column.endswith(f"-{suffix}") for suffix in suffixes)]
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

    def _build_promql(self, question: str, selected_columns: List[str], services: List[str]) -> str:
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
        if metric_suffix == "mem":
            pod_filter = f',pod=~"{target_service}.*"' if target_service else ""
            return f'sum(container_memory_working_set_bytes{{container!="POD"{pod_filter}}}) by (pod, namespace)'
        if metric_suffix.startswith("network"):
            pod_filter = f',pod=~"{target_service}.*"' if target_service else ""
            return f'sum(rate(container_network_receive_bytes_total{{{self._network_selector(pod_filter)}}}[5m])) by (pod)'
        if metric_suffix == "success_rate" or any(token in text for token in ["error", "成功率", "availability"]):
            svc_filter = f',service="{target_service}"' if target_service else ""
            return (
                f'sum(rate(http_requests_total{{status!~"5.."{svc_filter}}}[5m])) '
                f'/ sum(rate(http_requests_total{{{self._drop_leading_comma(svc_filter)}}}[5m]))'
            )
        return "sum(up)"

    def _pick_service(self, question: str, services: List[str]) -> Optional[str]:
        text = str(question or "").lower()
        for service in services:
            if service.lower() in text:
                return service
        return services[0] if services else None

    def _network_selector(self, pod_filter: str) -> str:
        selector = f'container!="POD"{pod_filter}'
        return selector

    def _drop_leading_comma(self, value: str) -> str:
        return value[1:] if value.startswith(",") else value
