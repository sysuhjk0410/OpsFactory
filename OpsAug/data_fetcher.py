from __future__ import annotations

"""
Multi-modal data fetcher for OpsAug.

This module provides "raw/long" intermediate formats that can be consumed by
`data_preprocess.py` to build ART-compatible samples.

Notes:
- Metrics: fetch K8s Pod golden metrics from CMS and return a long table
  (time, instance, metric, value).
- Logs: fetch log entries from SLS and return a long table
  (time, instance, level, message, source).
- Traces: fetch span-like trace records from CMS (using the same SPL patterns
  as the trace_tools wrapper does), and return a long table
  (start_time, end_time, instance, operation_name, duration_ms, status_code).

The exact "instance" naming must match ART's `node_dict` ordering in
`data_preprocess.py`. If your environment uses different entity id formats,
provide an `instance_mapper` or normalize before preprocessing.
"""

from dataclasses import dataclass
import ast
import json
import os
import re
from datetime import datetime
from typing import Callable, Optional, Sequence, Any, Union

import pandas as pd


def _parse_time_arg(t: Union[str, int]) -> int:
    """Parse unix timestamp (sec or ms) or datetime string to unix seconds."""
    if isinstance(t, int):
        # Heuristic: ms
        return int(t // 1000) if t > 1e11 else int(t)
    if isinstance(t, str):
        t = t.strip()
        # Accept "now" / "now-5m" / "now-1h" style
        if t.lower() == "now":
            return int(datetime.now().timestamp())
        if t.lower().startswith("now-"):
            m = re.match(r"now-(\d+)([smhd])$", t.lower())
            if m:
                amount = int(m.group(1))
                unit = m.group(2)
                mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
                return int((datetime.now().timestamp()) - amount * mult)
        # Try datetime string
        try:
            dt = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
            return int(dt.timestamp())
        except ValueError:
            pass
        # Fallback: numeric string
        return int(float(t))
    raise TypeError(f"Unsupported time type: {type(t)}")


def _bucket_time(ts: int, bucket_seconds: int) -> int:
    return int((ts // bucket_seconds) * bucket_seconds)


def _extract_json_maybe(s: Any) -> dict:
    if s is None:
        return {}
    if isinstance(s, dict):
        return s
    if isinstance(s, str):
        s = s.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return {}
    return {}


def _safe_literal_list(x: Any) -> list:
    """Parse CMS `__ts__` / `__value__` string arrays."""
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return list(x)
    if isinstance(x, str):
        try:
            return list(ast.literal_eval(x))
        except (ValueError, SyntaxError):
            return []
    return []


def _create_cms_client(region: str, env_file: Optional[str] = None):
    """
    Create a CMS client.

    We intentionally reuse the same approach as `metric_tools.data_fetcher`
    to keep compatibility with your environment variables.
    """
    from metric_tools.data_fetcher import _create_cms_client as _tool_create

    return _tool_create(region=region, env_file=env_file)


def _execute_cms_query(cms_client, workspace: str, query: str, from_ts: int, to_ts: int, limit: int):
    from metric_tools.data_fetcher import _execute_cms_query as _tool_exec

    return _tool_exec(
        cms_client=cms_client,
        workspace=workspace,
        query=query,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=limit,
    )


def fetch_k8s_metrics_long(
    from_time: Union[str, int],
    to_time: Union[str, int],
    region: str = "cn-hongkong",
    workspace: str = "rca-benchmark",
    step: str = "30s",
    env_file: Optional[str] = None,
    metric_names: Optional[Sequence[str]] = None,
    instance_mapper: Optional[Callable[[str], str]] = None,
    limit: int = 10000,
) -> pd.DataFrame:
    """
    Fetch K8s Pod metrics from CMS as a long table.

    Returns columns:
      - time (unix seconds)
      - instance (pod name by default)
      - metric (raw metric name from CMS)
      - value (float)
    """
    from_ts = _parse_time_arg(from_time)
    to_ts = _parse_time_arg(to_time)

    cms_client = _create_cms_client(region, env_file)

    query = (
        ".entity_set with(domain='k8s', name='k8s.pod') "
        f"| entity-call get_golden_metrics('range', '{step}', false) "
        f"| limit {limit}"
    )
    items = _execute_cms_query(cms_client, workspace, query, from_ts, to_ts, limit=limit)

    metric_set = set(metric_names) if metric_names else None
    rows: list[dict[str, Any]] = []
    for item in items:
        labels = _extract_json_maybe(item.get("__labels__", "{}"))
        pod_name = labels.get("name", "") or labels.get("pod_name", "")
        if not pod_name:
            continue
        metric = item.get("metric", "")
        if not metric:
            continue
        if metric_set is not None and metric not in metric_set:
            continue

        ts_list = _safe_literal_list(item.get("__ts__", "[]"))
        val_list = _safe_literal_list(item.get("__value__", "[]"))
        if not ts_list or not val_list:
            continue

        pod_name_norm = instance_mapper(pod_name) if instance_mapper else pod_name
        for ts, val in zip(ts_list, val_list):
            # metric_tools parses ts similarly; reuse it for unit normalization
            try:
                ts_int = int(ts)
            except Exception:
                continue
            ts_sec = ts_int // 1_000_000_000 if ts_int > 1e15 else (ts_int // 1000 if ts_int > 1e12 else ts_int)
            rows.append(
                {
                    "time": ts_sec,
                    "instance": pod_name_norm,
                    "metric": metric,
                    "value": float(val) if val is not None and val != "" else 0.0,
                }
            )
    return pd.DataFrame(rows)


def fetch_apm_metrics_long(
    from_time: Union[str, int],
    to_time: Union[str, int],
    region: str = "cn-hongkong",
    workspace: str = "rca-benchmark",
    step: str = "30s",
    env_file: Optional[str] = None,
    metric_names: Optional[Sequence[str]] = None,
    instance_mapper: Optional[Callable[[str], str]] = None,
    limit: int = 10000,
) -> pd.DataFrame:
    """
    Fetch APM service golden metrics as long table.

    This returns `instance` as service name by default.
    """
    from_ts = _parse_time_arg(from_time)
    to_ts = _parse_time_arg(to_time)
    cms_client = _create_cms_client(region, env_file)

    # Map entity_id -> service name
    entity_query = (
        ".entity_set with(domain='apm', name='apm.service') "
        "| entity-call get_entities() "
        f"| limit 100"
    )
    entity_items = _execute_cms_query(cms_client, workspace, entity_query, from_ts, to_ts, limit=100)
    entity_to_service = {}
    for item in entity_items:
        svc = item.get("service", "") or item.get("name", "")
        eid = item.get("__entity_id__", "") or item.get("entity_id", "")
        if svc and eid:
            entity_to_service[str(eid)] = str(svc)

    metric_set = set(metric_names) if metric_names else None
    query = (
        ".entity_set with(domain='apm', name='apm.service') "
        f"| entity-call get_golden_metrics('range', '{step}', false) "
        f"| limit {limit}"
    )
    items = _execute_cms_query(cms_client, workspace, query, from_ts, to_ts, limit=limit)

    rows: list[dict[str, Any]] = []
    for item in items:
        metric = item.get("metric", "")
        if not metric:
            continue
        if metric_set is not None and metric not in metric_set:
            continue

        eid = str(item.get("__entity_id__", "") or "")
        svc_name = entity_to_service.get(eid, "")
        if not svc_name:
            continue
        inst = instance_mapper(svc_name) if instance_mapper else svc_name

        ts_list = _safe_literal_list(item.get("__ts__", "[]"))
        val_list = _safe_literal_list(item.get("__value__", "[]"))
        for ts, val in zip(ts_list, val_list):
            try:
                ts_int = int(ts)
            except Exception:
                continue
            ts_sec = ts_int // 1_000_000_000 if ts_int > 1e15 else (ts_int // 1000 if ts_int > 1e12 else ts_int)
            rows.append({"time": ts_sec, "instance": inst, "metric": metric, "value": float(val) if val else 0.0})

    return pd.DataFrame(rows)


def fetch_logs_long(
    from_time: Union[str, int],
    to_time: Union[str, int],
    region: str = "cn-qingdao",
    project: str = "tianchi-workspace",
    logstore: str = "default",
    query: str = "*",
    limit: int = 5000,
    instance_key: str = "entity_id",
    message_key: str = "message",
    level_key: str = "level",
    source_key: str = "source",
) -> pd.DataFrame:
    """
    Fetch SLS logs as long table.

    The returned schema is flexible:
      - time: unix seconds (if available from log fields)
      - instance: log[instance_key] if exists else "__unknown__"
      - level, message, source: derived from log fields when available
    """
    from common.sls_client import create_sls_client, execute_sls_query

    from_ts = _parse_time_arg(from_time)
    to_ts = _parse_time_arg(to_time)

    log_client = create_sls_client(region=region)
    resp = execute_sls_query(
        log_client=log_client,
        project_name=project,
        logstore_name=logstore,
        query=query,
        from_time=from_ts,
        to_time=to_ts,
        limit=limit,
    )
    if resp.get("error"):
        return pd.DataFrame(columns=["time", "instance", "level", "message", "source"])

    rows = []
    for d in resp.get("data", []):
        # tracing/logstore 常见实例字段兜底：entity_id -> serviceName -> service -> hostname -> pid
        instance = d.get(instance_key)
        if not instance:
            instance = (
                d.get("serviceName")
                or d.get("service")
                or d.get("hostname")
                or d.get("pid")
                or "__unknown__"
            )
        instance = str(instance)
        level = d.get(level_key, None)
        message = d.get(message_key, None)
        source = d.get(source_key, None)

        # Some SDKs already produce numeric time fields; others don't.
        # We'll try common keys.
        t = (
            d.get("time")
            or d.get("timestamp")
            or d.get("ts")
            or d.get("@timestamp")
            or d.get("startTime")
            or d.get("endTime")
        )
        try:
            t = int(t) if t is not None else None
        except Exception:
            t = None
        # Normalize ns/ms -> seconds
        if t is not None:
            if t > 1e15:
                t = t // 1_000_000_000
            elif t > 1e12:
                t = t // 1000

        rows.append({"time": t, "instance": instance, "level": level, "message": message, "source": source})
    df = pd.DataFrame(rows)
    # Drop rows without time; preprocess expects bucket time
    if "time" in df.columns:
        df = df.dropna(subset=["time"])
        df["time"] = df["time"].astype(int)
    return df


@dataclass
class TraceSpanRecord:
    trace_id: str
    span_id: Optional[str]
    instance: str
    operation_name: Optional[str]
    start_time: Optional[int]
    end_time: Optional[int]
    duration_ms: Optional[float]
    status_code: Optional[str]


def _build_entity_ids_param(entity_ids: Optional[Sequence[str]]) -> str:
    if not entity_ids:
        return ""
    clean = [str(x).strip() for x in entity_ids if x and str(x).strip()]
    if not clean:
        return ""
    quoted = [f"'{x}'" for x in clean]
    return f", ids=[{','.join(quoted)}]"


def fetch_traces_spans_long(
    from_time: Union[str, int],
    to_time: Union[str, int],
    domain: str = "apm",
    entity_set_name: str = "apm.service",
    trace_set_domain: str = "apm",
    trace_set_name: str = "apm.trace.common",
    entity_ids: Optional[Sequence[str]] = None,
    has_error: Optional[bool] = None,
    min_duration_ms: Optional[float] = None,
    max_duration_ms: Optional[float] = None,
    limit: int = 50,
    detail_limit: int = 1000,
    region: str = "cn-qingdao",
    workspace: str = "tianchi-workspace",
    env_file: Optional[str] = None,
) -> pd.DataFrame:
    """
    Fetch trace span records as long table.

    The implementation uses two CMS queries:
      1) Search traces (returns traceId + duration_ms + span_count + error count)
      2) Fetch detailed spans for selected traceIds

    Returns columns:
      - start_time, end_time, instance, operation_name, duration_ms, status_code
    """
    from_ts = _parse_time_arg(from_time)
    to_ts = _parse_time_arg(to_time)
    cms_client = _create_cms_client(region=region, env_file=env_file)

    entity_ids_param = _build_entity_ids_param(entity_ids)

    filter_params = []
    if min_duration_ms is not None:
        filter_params.append(f"cast(duration as bigint) > {int(min_duration_ms * 1000000)}")
    if max_duration_ms is not None:
        filter_params.append(f"cast(duration as bigint) < {int(max_duration_ms * 1000000)}")
    if has_error is not None:
        filter_params.append("cast(statusCode as varchar) = '2'" if has_error else "cast(statusCode as varchar) <> '2'")

    filter_param_str = ""
    if filter_params:
        filter_param_str = "| where " + " and ".join(filter_params)

    stats_str = (
        "| extend duration_ms = cast(duration as double) / 1000000, "
        "is_error = case when cast(statusCode as varchar) = '2' then 1 else 0 end | "
        "stats span_count = count(1), error_span_count = sum(is_error), duration_ms = max(duration_ms) by traceId | "
        "sort duration_ms desc, error_span_count desc | "
        "project traceId, duration_ms, span_count, error_span_count"
    )

    limit_value = int(limit) if limit and limit > 0 else 50
    query_search = (
        f".entity_set with(domain='{domain}', name='{entity_set_name}'{entity_ids_param}) "
        f"| entity-call get_trace('{trace_set_domain}', '{trace_set_name}') "
        f"{filter_param_str} {stats_str} | limit {limit_value}"
    )
    search_items = _execute_cms_query(cms_client, workspace, query_search, from_ts, to_ts, limit=limit_value)
    trace_ids = [str(x.get("traceId") or x.get("trace_id") or "") for x in search_items]
    trace_ids = [x for x in trace_ids if x]

    if not trace_ids:
        return pd.DataFrame(columns=["start_time", "end_time", "instance", "operation_name", "duration_ms", "status_code"])

    quoted_filters = [f"traceId='{tid}'" for tid in trace_ids]
    trace_ids_param = " or ".join(quoted_filters)

    query_detail = (
        f".entity_set with(domain='{domain}', name='{entity_set_name}') "
        f"| entity-call get_trace('{trace_set_domain}', '{trace_set_name}') "
        f"| where {trace_ids_param} "
        "| extend duration_ms = cast(duration as double) / 1000000 "
        "| project-away duration "
        f"| sort traceId desc, duration_ms desc "
        f"| limit {detail_limit}"
    )
    span_items = _execute_cms_query(cms_client, workspace, query_detail, from_ts, to_ts, limit=detail_limit)

    rows = []
    for d in span_items:
        # entity/service field name varies; try common ones
        inst = (
            d.get("entity_id")
            or d.get("__entity_id__")
            or d.get("entityId")
            or d.get("entity")
            or d.get("serviceName")
            or d.get("service")
            or d.get("hostname")
            or d.get("pid")
        )
        # resources 里常见 app_kubernetes_io_component / service.name 等
        if not inst and d.get("resources") is not None:
            resources = d.get("resources")
            if isinstance(resources, str):
                try:
                    resources = json.loads(resources)
                except Exception:
                    resources = {}
            if isinstance(resources, dict):
                inst = (
                    resources.get("app_kubernetes_io_component")
                    or resources.get("service.name")
                    or resources.get("k8s.pod.name")
                    or resources.get("k8s.container.name")
                )

        inst = str(inst) if inst else "__unknown__"
        op = (
            d.get("operation_name")
            or d.get("operationName")
            or d.get("operation")
            or d.get("spanName")
            or d.get("name")
        )
        status_code = d.get("statusCode") or d.get("status_code")
        start_time = d.get("startTime") or d.get("start_time") or d.get("start")
        end_time = d.get("endTime") or d.get("end_time") or d.get("end")
        duration_ms = d.get("duration_ms") or d.get("durationMs") or d.get("durationMs".lower())
        # Normalize time units
        try:
            start_time = int(start_time) if start_time is not None else None
        except Exception:
            start_time = None
        try:
            end_time = int(end_time) if end_time is not None else None
        except Exception:
            end_time = None
        if duration_ms is not None:
            try:
                duration_ms = float(duration_ms)
            except Exception:
                duration_ms = None
        rows.append(
            {
                "start_time": start_time,
                "end_time": end_time,
                "instance": inst,
                "operation_name": op,
                "duration_ms": duration_ms,
                "status_code": str(status_code) if status_code is not None else None,
            }
        )

    return pd.DataFrame(rows)

