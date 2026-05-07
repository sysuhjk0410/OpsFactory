# -*- coding: utf-8 -*-
"""Shared fault-injection scheduling metadata for dynamic data sources."""

from __future__ import annotations

import time
from typing import Any, Dict


def _to_int(value: Any, default: int, min_value: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    return max(min_value, number)


def build_fault_injection_window(
    *,
    source_id: str,
    fault_type: str,
    target: str,
    kwargs: Dict[str, Any] | None = None,
    default_mode: str = "simulated_replay",
) -> Dict[str, Any]:
    """Normalize UI/API fault-injection timing into a durable case contract."""

    kwargs = kwargs or {}
    duration = _to_int(kwargs.get("duration_seconds", kwargs.get("duration", 180)), 180, 10)
    observation = _to_int(
        kwargs.get("observation_window_seconds", kwargs.get("observation_window", duration + 120)),
        duration + 120,
        duration,
    )
    pre_window = _to_int(kwargs.get("pre_window_seconds", 60), 60, 0)
    interval = _to_int(kwargs.get("collection_interval_seconds", kwargs.get("interval_seconds", 15)), 15, 1)
    scheduled_at = (
        kwargs.get("scheduled_at")
        or kwargs.get("start_time")
        or kwargs.get("injection_time")
        or time.strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    requested_mode = kwargs.get("injection_mode") or kwargs.get("mode") or default_mode

    return {
        "source_id": source_id,
        "fault_type": fault_type,
        "target": target,
        "scheduled_at": str(scheduled_at),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_seconds": duration,
        "observation_window_seconds": observation,
        "pre_window_seconds": pre_window,
        "post_window_seconds": max(0, observation - duration),
        "collection_interval_seconds": interval,
        "requested_mode": str(requested_mode),
        "traffic_profile": kwargs.get("traffic_profile", "steady"),
        "parameters": {
            k: v
            for k, v in kwargs.items()
            if k
            not in {
                "scheduled_at",
                "start_time",
                "injection_time",
                "duration",
                "duration_seconds",
                "observation_window",
                "observation_window_seconds",
                "pre_window_seconds",
                "collection_interval_seconds",
                "interval_seconds",
                "injection_mode",
                "mode",
                "traffic_profile",
            }
        },
    }


def finalize_fault_injection_window(
    injection: Dict[str, Any],
    *,
    status: str,
    message: str,
) -> Dict[str, Any]:
    """Mark whether the request executed against a real cluster."""

    actual = status == "injected"
    injection = dict(injection)
    injection.update(
        {
            "status": status,
            "actual_cluster_injection": actual,
            "execution_mode": "live_kubernetes" if actual else "failed_real_injection",
            "message": message,
            "honesty_note": (
                "已通过 Kubernetes/Chaos 命令作用到目标平台。"
                if actual
                else "真实故障注入未执行成功；系统不会把失败请求伪装成仿真故障案例。请确认 kubectl、命名空间和目标服务可访问。"
            ),
        }
    )
    return injection


def injection_capability(source_id: str, source_name: str, source_type: str) -> Dict[str, Any]:
    """Describe what the UI should honestly claim about a source."""

    if source_type != "dynamic":
        return {
            "supports_fault_injection": False,
            "supports_time_window": False,
            "mode": "static_snapshot_replay",
            "note": "静态数据源只能回放已有快照，不会执行真实集群故障注入。",
        }
    return {
        "supports_fault_injection": True,
        "supports_time_window": True,
        "supports_continuous_collection": True,
        "mode": "live_kubernetes_required",
        "note": f"{source_name} 仅执行真实 Kubernetes 故障注入；kubectl、目标集群或命名空间不可用时会直接失败，不会退回仿真。",
    }
