# -*- coding: utf-8 -*-
"""DrainMCP adapter for Ops Factory — log modality fault warning, localization, and diagnosis.

https://github.com/NickLennonLiu/drain_mcp

DrainMCP is a log single-modality fault analysis tool for cloud-native systems.
Its main capabilities include:
1. Fault warning from service logs
2. Fault localization from service logs
3. Fault diagnosis from service logs

Integrated as a tool in SRE's RCA pipeline.
"""

from __future__ import annotations

import json
import random
import re
import subprocess
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from .base_data_source import BaseDataSource


class DrainMCPAdapter:
    """Adapter for the DrainMCP log analysis tool.

    Processes log evidence from any data source to detect anomalies,
    localize faults, and diagnose root causes from log patterns.
    """

    TOOL_NAME = "DrainMCP"
    TOOL_DESCRIPTION = (
        "面向云原生系统的日志单模态故障预警、定位、诊断模型及工具。"
        "主要功能包括对云原生系统的服务日志的故障预警、故障定位与故障诊断。"
    )

    # Drain log parsing keyword patterns for anomaly detection
    DRAIN_PATTERNS = {
        "OOM": re.compile(r"OOM|out of memory|memory limit exceeded", re.IGNORECASE),
        "crash": re.compile(r"crash|CrashLoopBackOff|exit code|signal.*kill", re.IGNORECASE),
        "timeout": re.compile(r"timeout|timed out|deadline exceeded", re.IGNORECASE),
        "connection": re.compile(r"connection refused|ECONNREFUSED|connection reset|broken pipe", re.IGNORECASE),
        "disk": re.compile(r"no space left|disk full|write error", re.IGNORECASE),
        "database": re.compile(r"database.*error|connection pool|too many connections|SQLException", re.IGNORECASE),
        "network": re.compile(r"network.*unreachable|DNS.*fail|ENETUNREACH|ENOTFOUND", re.IGNORECASE),
        "throttle": re.compile(r"throttl|cfs quota|CPU throttl", re.IGNORECASE),
        "auth": re.compile(r"unauthorized|forbidden|auth.*fail|permission denied", re.IGNORECASE),
        "null": re.compile(r"NullPointerException|NoneType|undefined is not|TypeError", re.IGNORECASE),
    }

    SEVERITY_MAP = {
        "OOM": "critical",
        "crash": "critical",
        "timeout": "warning",
        "connection": "critical",
        "disk": "critical",
        "database": "critical",
        "network": "critical",
        "throttle": "warning",
        "auth": "warning",
        "null": "warning",
    }

    def __init__(self, data_source: BaseDataSource):
        self.data_source = data_source

    def analyze(self, case_id: str) -> Dict[str, Any]:
        """Run full DrainMCP analysis pipeline on a fault case.

        Steps:
        1. Parse logs with Drain-style template extraction
        2. Detect anomalous log patterns (fault warning)
        3. Localize faults by correlating log errors with services
        4. Diagnose root cause from log evidence
        """
        detail = self.data_source.get_case_detail(case_id)
        logs = detail.get("logs", {})
        entries = logs.get("entries", []) if isinstance(logs, dict) else []

        if not entries:
            return {
                "tool": self.TOOL_NAME,
                "case_id": case_id,
                "status": "no_data",
                "summary": "DrainMCP: 未获取到日志数据，无法进行分析。",
            }

        # Step 1: Drain-style log parsing
        parsed = self._drain_parse(entries)

        # Step 2: Fault warning from anomalous patterns
        warnings = self._detect_fault_warnings(parsed)

        # Step 3: Fault localization
        localizations = self._localize_faults(parsed, warnings)

        # Step 4: Root cause diagnosis
        diagnosis = self._diagnose_root_cause(parsed, warnings, localizations)

        return {
            "tool": self.TOOL_NAME,
            "case_id": case_id,
            "source": detail.get("source", self.data_source.name),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "log_count": len(entries),
            "parsed_templates": len(parsed.get("templates", {})),
            "anomalous_patterns": len(parsed.get("anomalous_entries", [])),
            "fault_warnings": warnings,
            "fault_localizations": localizations,
            "root_cause_diagnosis": diagnosis,
            "summary": self._build_summary(warnings, localizations, diagnosis),
            "rca_candidates": self._extract_rca_candidates(localizations, diagnosis),
        }

    def _drain_parse(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Drain-style log parsing: extract templates from raw log entries.

        Drain is a log parsing algorithm that clusters logs into templates
        by extracting variable parts (timestamps, IPs, numbers, etc.).
        """
        templates: Dict[str, List[Dict[str, Any]]] = {}
        anomalous: List[Dict[str, Any]] = []
        normal: List[Dict[str, Any]] = []

        var_pattern = re.compile(
            r"(\d+\.\d+\.\d+\.\d+(?::\d+)?)"  # IP:port
            r"|(\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b)"  # UUID
            r"|(\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}.*?\b)"  # timestamp
            r"|(\b\d+(?:\.\d+)?(?:ms|s|MB|GB|KB|%|)\b)"  # numbers with units
        )

        for entry in entries:
            message = entry.get("message", "")
            level = entry.get("level", "INFO").upper()
            service = entry.get("service", "unknown")

            # Normalize: replace variables with <*>
            normalized = var_pattern.sub("<*>", message)
            normalized = re.sub(r"\s+", " ", normalized).strip()

            templates.setdefault(normalized, []).append(entry)

            if level in ("ERROR", "CRITICAL", "FATAL", "WARN", "WARNING"):
                anomalous.append({
                    **entry,
                    "template": normalized,
                    "matched_patterns": self._match_drain_patterns(message),
                })
            else:
                normal.append(entry)

        return {
            "templates": {k: len(v) for k, v in templates.items()},
            "templates_with_samples": {
                k: v[:3] for k, v in templates.items()
            },
            "anomalous_entries": anomalous,
            "normal_entries": normal,
            "template_count": len(templates),
        }

    def _match_drain_patterns(self, message: str) -> List[str]:
        """Match a log message against known fault patterns."""
        matched = []
        for pattern_name, regex in self.DRAIN_PATTERNS.items():
            if regex.search(message):
                matched.append(pattern_name)
        return matched

    def _detect_fault_warnings(self, parsed: Dict) -> List[Dict[str, Any]]:
        """Detect fault warning signals from anomalous log entries."""
        anomalous = parsed.get("anomalous_entries", [])
        if not anomalous:
            return []

        # Count pattern occurrences across services
        pattern_service_counter = Counter()
        service_counter = Counter()
        pattern_details: Dict[str, List[str]] = {}

        for entry in anomalous:
            service = entry.get("service", "unknown")
            level = entry.get("level", "").upper()
            service_counter[service] += 1

            for pattern in entry.get("matched_patterns", []):
                key = f"{service}:{pattern}"
                pattern_service_counter[key] += 1
                pattern_details.setdefault(pattern, []).append(entry.get("message", "")[:200])

        warnings = []
        for key, count in pattern_service_counter.most_common(10):
            parts = key.split(":", 1)
            service = parts[0]
            pattern = parts[1] if len(parts) > 1 else "unknown"
            severity = self.SEVERITY_MAP.get(pattern, "warning")
            sample_msgs = pattern_details.get(pattern, [])[:3]
            warnings.append({
                "service": service,
                "pattern": pattern,
                "count": count,
                "severity": severity,
                "sample_messages": sample_msgs,
                "warning_type": "log_anomaly",
                "description": (
                    f"服务 {service} 在日志中出现 {count} 次 {pattern} 类型异常，"
                    f"严重级别: {severity}。"
                ),
            })

        # Sort by severity: critical first, then by count
        severity_order = {"critical": 0, "warning": 1}
        warnings.sort(key=lambda w: (severity_order.get(w["severity"], 2), -w["count"]))

        return warnings

    def _localize_faults(
        self, parsed: Dict, warnings: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Localize faults to specific services based on log patterns."""
        anomalous = parsed.get("anomalous_entries", [])
        if not anomalous:
            return []

        # Count error/warning entries per service
        service_errors = Counter()
        for entry in anomalous:
            service = entry.get("service", "unknown")
            service_errors[service] += 1

        localizations = []
        for service, error_count in service_errors.most_common(10):
            # Find related warnings for this service
            related_warnings = [w for w in warnings if w["service"] == service]
            patterns = [w["pattern"] for w in related_warnings]

            # Determine localization confidence based on error density
            total_for_service = error_count
            confidence = min(0.95, 0.3 + 0.1 * total_for_service)

            localizations.append({
                "service": service,
                "error_count": error_count,
                "detected_patterns": patterns,
                "confidence": round(confidence, 3),
                "is_primary_suspect": confidence > 0.7,
                "reason": (
                    f"日志错误集中在 {service}，"
                    f"检测到 {', '.join(patterns) if patterns else '异常模式'}，"
                    f"置信度 {confidence:.2f}"
                ),
            })

        return localizations

    def _diagnose_root_cause(
        self, parsed: Dict, warnings: List[Dict[str, Any]],
        localizations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Diagnose root cause from log evidence."""
        if not localizations:
            return {"diagnosis": "无足够日志证据确定根因", "confidence": 0.0}

        # Primary suspect: service with highest error count
        primary = localizations[0] if localizations else None
        if not primary:
            return {"diagnosis": "无法从日志中定位故障", "confidence": 0.0}

        patterns = primary.get("detected_patterns", [])
        fault_type = self._infer_fault_type_from_patterns(patterns)

        diagnosis = {
            "primary_suspect": primary["service"],
            "error_count": primary["error_count"],
            "detected_patterns": patterns,
            "inferred_fault_type": fault_type,
            "confidence": primary["confidence"],
            "diagnosis": (
                f"根据 DrainMCP 日志分析，故障根因最可能位于服务 {primary['service']}。"
                f"检测到 {','.join(patterns)} 类型的日志异常，"
                f"推断故障类型为 {fault_type}，置信度 {primary['confidence']:.2f}。"
            ),
            "evidence_summary": (
                f"共分析异常日志 {len(parsed.get('anomalous_entries', []))} 条，"
                f"识别到 {len(parsed.get('templates', {}))} 种日志模板。"
                f"主要异常集中在 {primary['service']} (共 {primary['error_count']} 条异常日志)。"
            ),
        }

        return diagnosis

    def _infer_fault_type_from_patterns(self, patterns: List[str]) -> str:
        """Infer fault type from Drain pattern matches."""
        pattern_to_fault = {
            "OOM": "memory_leak",
            "crash": "pod_crash",
            "timeout": "high_latency",
            "connection": "service_unavailable",
            "disk": "disk_full",
            "database": "database_error",
            "network": "network_partition",
            "throttle": "high_cpu",
            "auth": "auth_failure",
            "null": "application_error",
        }
        types = [pattern_to_fault.get(p, "unknown") for p in patterns]
        if types:
            return Counter(types).most_common(1)[0][0]
        return "unknown"

    def _build_summary(
        self, warnings: List[Dict], localizations: List[Dict],
        diagnosis: Dict
    ) -> str:
        """Build human-readable summary."""
        parts = ["DrainMCP 日志单模态分析结果："]
        if warnings:
            parts.append(
                f"检测到 {len(warnings)} 个故障预警信号，"
                f"主要涉及服务 {', '.join(set(w['service'] for w in warnings[:3]))}。"
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
                "evidence": f"DrainMCP 日志分析：{loc['reason']}",
                "tool": self.TOOL_NAME,
            })
        return candidates
