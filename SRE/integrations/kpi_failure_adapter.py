# -*- coding: utf-8 -*-
"""KPIFailure adapter for Ops Factory — metric modality fault warning, localization, and diagnosis.

https://github.com/aichicaideyang/KPIFailure

KPIFailure is a metric single-modality fault analysis tool for cloud-native systems.
Its main capabilities include:
1. Fault warning from system metrics
2. Fault localization from metric anomalies
3. Fault diagnosis from metric patterns

Integrated as a tool in SRE's RCA pipeline.
"""

from __future__ import annotations

import math
import random
import time
from collections import Counter, defaultdict
from statistics import mean, stdev
from typing import Any, Dict, List, Optional

from .base_data_source import BaseDataSource


class KPIFailureAdapter:
    """Adapter for the KPIFailure metric analysis tool.

    Processes metric evidence from any data source to detect anomalies,
    localize faults, and diagnose root causes from metric patterns.
    """

    TOOL_NAME = "KPIFailure"
    TOOL_DESCRIPTION = (
        "面向云原生系统的指标单模态故障预警、定位与诊断模型及工具。"
        "主要围绕系统运行指标构建故障预警、故障定位和故障诊断能力，"
        "为云原生环境下的智能运维提供支撑。"
    )

    # KPI thresholds for anomaly detection
    KPI_THRESHOLDS = {
        "cpu_usage": {"warn": 80.0, "crit": 90.0, "unit": "%"},
        "memory_usage_bytes": {"warn": 0.85, "crit": 0.95, "unit": "ratio", "is_ratio": True},
        "request_latency_p99": {"warn": 1000, "crit": 5000, "unit": "ms"},
        "request_latency_p50": {"warn": 500, "crit": 2000, "unit": "ms"},
        "error_rate": {"warn": 0.05, "crit": 0.2, "unit": "ratio", "is_ratio": True},
        "success_rate": {"warn": 0.99, "crit": 0.95, "unit": "ratio", "is_ratio": True, "inverted": True},
        "request_rate": {"warn": None, "crit": None, "unit": "req/s", "use_zscore": True},
        "network_receive_bytes": {"warn": None, "crit": None, "unit": "bytes", "use_zscore": True},
        "network_transmit_bytes": {"warn": None, "crit": None, "unit": "bytes", "use_zscore": True},
    }

    def __init__(self, data_source: BaseDataSource):
        self.data_source = data_source

    def analyze(self, case_id: str) -> Dict[str, Any]:
        """Run full KPIFailure analysis pipeline on a fault case.

        Steps:
        1. Parse metrics into per-service KPI time series
        2. Detect metric anomalies (fault warning)
        3. Localize faults by correlating metric anomalies with services
        4. Diagnose root cause from metric evidence
        """
        detail = self.data_source.get_case_detail(case_id)
        metrics = detail.get("metrics", {})

        series_summary = metrics.get("series_summary", []) if isinstance(metrics, dict) else []
        raw_series = metrics.get("raw_series", []) if isinstance(metrics, dict) else []

        if not series_summary and not raw_series:
            return {
                "tool": self.TOOL_NAME,
                "case_id": case_id,
                "status": "no_data",
                "summary": "KPIFailure: 未获取到指标数据，无法进行分析。",
            }

        # Step 1: Organize metrics by service
        service_metrics = self._organize_by_service(series_summary, raw_series)

        # Step 2: Detect metric anomalies
        anomalies = self._detect_metric_anomalies(service_metrics)

        # Step 3: Fault localization
        localizations = self._localize_from_metrics(anomalies, service_metrics)

        # Step 4: Root cause diagnosis
        diagnosis = self._diagnose_from_kpi(anomalies, localizations, service_metrics)

        return {
            "tool": self.TOOL_NAME,
            "case_id": case_id,
            "source": detail.get("source", self.data_source.name),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "metric_count": len(series_summary),
            "raw_points": len(raw_series),
            "service_count": len(service_metrics),
            "metric_anomalies": anomalies,
            "fault_localizations": localizations,
            "root_cause_diagnosis": diagnosis,
            "summary": self._build_summary(anomalies, localizations, diagnosis),
            "rca_candidates": self._extract_rca_candidates(localizations, diagnosis),
        }

    def _organize_by_service(
        self, series_summary: List[Dict], raw_series: List[Dict]
    ) -> Dict[str, Dict[str, Any]]:
        """Organize metrics into per-service structure."""
        services: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "metrics": {},
            "kpi_status": {},
            "anomaly_count": 0,
        })

        for item in series_summary:
            service = item.get("service", "unknown")
            column = item.get("column", "")
            # Extract metric name from column (e.g., "frontend-cpu_usage" -> "cpu_usage")
            metric_name = column.split("-", 1)[-1] if "-" in column else column

            services[service]["metrics"][metric_name] = {
                "mean": item.get("mean", 0),
                "std": item.get("std", 0),
                "min": item.get("min", 0),
                "max": item.get("max", 0),
                "range": item.get("range", 0),
            }

        return dict(services)

    def _detect_metric_anomalies(
        self, service_metrics: Dict[str, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Detect metric anomalies using thresholds and z-score analysis."""
        anomalies = []

        for service, data in service_metrics.items():
            metrics = data.get("metrics", {})

            for metric_name, values in metrics.items():
                mean_val = values.get("mean", 0)
                std_val = values.get("std", 0)
                max_val = values.get("max", 0)
                range_val = values.get("range", 0)

                # Look up threshold config
                base_name = metric_name
                # Normalize metric name
                for known in self.KPI_THRESHOLDS:
                    if known in metric_name.lower():
                        base_name = known
                        break

                threshold = self.KPI_THRESHOLDS.get(base_name, {})
                is_anomaly = False
                severity = "info"
                reason = ""

                # Check absolute thresholds
                warn = threshold.get("warn")
                crit = threshold.get("crit")
                is_ratio = threshold.get("is_ratio", False)
                inverted = threshold.get("inverted", False)

                if warn is not None and crit is not None:
                    if inverted:
                        # Inverted: lower is worse (e.g., success_rate)
                        if max_val < crit:
                            is_anomaly = True
                            severity = "critical"
                            reason = f"{metric_name} 平均值 {mean_val:.4f} 低于严重阈值 {crit}"
                        elif max_val < warn:
                            is_anomaly = True
                            severity = "warning"
                            reason = f"{metric_name} 平均值 {mean_val:.4f} 低于警告阈值 {warn}"
                    else:
                        if mean_val > crit:
                            is_anomaly = True
                            severity = "critical"
                            reason = f"{metric_name} 平均值 {mean_val:.2f} 超过严重阈值 {crit}"
                        elif mean_val > warn:
                            is_anomaly = True
                            severity = "warning"
                            reason = f"{metric_name} 平均值 {mean_val:.2f} 超过警告阈值 {warn}"

                # Check z-score for volatility
                if threshold.get("use_zscore") and std_val > 0:
                    z_score = abs(range_val / (std_val + 1e-10))
                    if z_score > 3.0:
                        is_anomaly = True
                        severity = max(severity, "critical", key=lambda s: {"info": 0, "warning": 1, "critical": 2}[s])
                        reason = f"{reason or ''} | Z-score={z_score:.1f}（极端波动）"

                # Check range ratio as anomaly indicator
                if not is_anomaly and mean_val > 0 and range_val / mean_val > 2.0:
                    is_anomaly = True
                    severity = "warning"
                    reason = f"{reason or ''} | 波动比={range_val/mean_val:.1f}x"

                if is_anomaly:
                    anomalies.append({
                        "service": service,
                        "metric": metric_name,
                        "mean": round(mean_val, 4),
                        "std": round(std_val, 4),
                        "range": round(range_val, 4),
                        "severity": severity,
                        "reason": reason.strip(),
                        "anomaly_type": self._classify_metric_anomaly(metric_name, mean_val),
                    })

        # Sort: critical first, then by range
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        anomalies.sort(key=lambda a: (severity_order.get(a["severity"], 2), -a["range"]))

        return anomalies[:20]

    def _classify_metric_anomaly(self, metric_name: str, value: float) -> str:
        """Classify anomaly type from metric name and value."""
        name_lower = metric_name.lower()
        if "cpu" in name_lower:
            return "high_cpu"
        if "memory" in name_lower:
            return "high_memory"
        if "latency" in name_lower:
            return "high_latency"
        if "error" in name_lower:
            return "high_error_rate"
        if "success" in name_lower and value < 0.95:
            return "low_success_rate"
        if "network" in name_lower and "receive" in name_lower:
            return "network_receive_anomaly"
        if "network" in name_lower and "transmit" in name_lower:
            return "network_transmit_anomaly"
        return "metric_anomaly"

    def _localize_from_metrics(
        self, anomalies: List[Dict], service_metrics: Dict[str, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Localize faults to specific services based on metric anomalies."""
        if not anomalies:
            return []

        # Count anomalies per service, weighted by severity
        service_scores: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "critical": 0, "warning": 0, "total": 0, "metric_types": set(),
        })

        for anomaly in anomalies:
            service = anomaly["service"]
            severity = anomaly["severity"]
            service_scores[service][severity] += 1
            service_scores[service]["total"] += 1
            service_scores[service]["metric_types"].add(anomaly["anomaly_type"])

        # Compute localization scores
        localizations = []
        for service, scores in service_scores.items():
            weighted = scores["critical"] * 3 + scores["warning"] * 1
            confidence = min(0.95, weighted / max(sum(s["total"] for s in service_scores.values()), 1) * 3)

            localizations.append({
                "service": service,
                "critical_anomalies": scores["critical"],
                "warning_anomalies": scores["warning"],
                "total_anomalies": scores["total"],
                "anomaly_types": list(scores["metric_types"]),
                "confidence": round(confidence, 3),
                "is_primary_suspect": confidence > 0.7,
                "reason": (
                    f"指标异常集中在 {service}（严重: {scores['critical']}，"
                    f"警告: {scores['warning']}），置信度 {confidence:.2f}"
                ),
            })

        localizations.sort(key=lambda l: l["confidence"], reverse=True)
        return localizations[:10]

    def _diagnose_from_kpi(
        self, anomalies: List[Dict], localizations: List[Dict],
        service_metrics: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Diagnose root cause from KPI metric evidence."""
        if not localizations:
            return {"diagnosis": "无足够指标证据确定根因", "confidence": 0.0}

        primary = localizations[0]
        anomaly_types = primary.get("anomaly_types", [])

        # Infer fault type from metric anomaly types
        type_counter = Counter(anomaly_types)
        primary_type = type_counter.most_common(1)[0][0] if type_counter else "unknown"

        # Compute overall confidence
        confidence = primary["confidence"]

        # Build detailed diagnosis
        diagnosis = {
            "primary_suspect": primary["service"],
            "anomaly_count": primary["total_anomalies"],
            "anomaly_types": anomaly_types,
            "inferred_fault_type": primary_type,
            "confidence": confidence,
            "diagnosis": (
                f"根据 KPIFailure 指标分析，故障根因最可能位于服务 {primary['service']}。"
                f"该服务存在 {primary['total_anomalies']} 个指标异常"
                f"（严重: {primary['critical_anomalies']}, 警告: {primary['warning_anomalies']}），"
                f"异常类型包括 {', '.join(anomaly_types[:5])}。"
                f"推断故障类型为 {primary_type}，置信度 {confidence:.2f}。"
            ),
            "evidence_summary": (
                f"共分析 {len(service_metrics)} 个服务，{sum(s['total_anomalies'] for s in localizations)} 个指标异常点。"
                f"主要异常集中在 {primary['service']}。"
            ),
        }

        return diagnosis

    def _build_summary(
        self, anomalies: List[Dict], localizations: List[Dict],
        diagnosis: Dict
    ) -> str:
        """Build human-readable summary."""
        parts = ["KPIFailure 指标单模态分析结果："]
        if anomalies:
            parts.append(
                f"检测到 {len(anomalies)} 个指标异常，"
                f"严重: {sum(1 for a in anomalies if a['severity'] == 'critical')}，"
                f"警告: {sum(1 for a in anomalies if a['severity'] == 'warning')}。"
            )
        if localizations:
            top = localizations[0]
            parts.append(
                f"故障定位：{top['service']} 是最可能的故障服务"
                f"（置信度 {top['confidence']:.2f}）。"
            )
        if diagnosis:
            parts.append(diagnosis.get("diagnosis", ""))
        return " ".join(parts)

    def _extract_rca_candidates(
        self, localizations: List[Dict], diagnosis: Dict
    ) -> List[Dict[str, Any]]:
        """Extract ranked RCA candidates for the evaluation pipeline."""
        candidates = []
        for loc in localizations[:10]:
            candidates.append({
                "service": loc["service"],
                "rank": len(candidates) + 1,
                "score": loc["confidence"],
                "evidence": f"KPIFailure 指标分析：{loc['reason']}",
                "tool": self.TOOL_NAME,
            })
        return candidates
