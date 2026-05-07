from __future__ import annotations

"""
Data preprocessing for OpsAug -> ART-compatible samples.

Goal:
Convert fetched 5-modalities (metrics/logs/traces + two extra modalities that you
may implement later) into the data format ART can consume:

    train_samples.pkl / test_samples.pkl
        Each sample is a tuple:
            (timestamp: int, graphs: dgl.DGLHeteroGraph, feats: torch.Tensor[N, 130])

Where:
- N == len(node_dict) instances (default D1: 46)
- 130 == channel_dim from ART config (metric/trace/log features concatenated
  in the order defined by `channel_dict.pkl`)

This module currently focuses on metrics/logs/traces-derived scalar features.
It uses ART's own `channel_dict.pkl` to decide which channels belong to:
- trace features (9 channels)
- log features (11 channels)
- metric features (remaining 110 channels)

For missing channels (not present in fetched data), we fill with zeros so that
the pipeline can still run.
"""

import os
import pickle
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Union

import pandas as pd


def _default_art_root() -> str:
    # opsaug_tools_v2/ART-master (embedded ART copy)
    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(here, "ART-master")


def _load_pkl(path: str) -> Any:
    with open(path, "rb") as f:
        return pickle.load(f)


def make_online_graph_template(num_nodes: int):
    """为线上动态 node_dict 生成一个与 D1 兼容的图模板（单一 ntype/etype: _N/_E）。

    D1 的图结构为 DGLHeteroGraph:
      ntypes: ['_N'], etypes: ['_E'], num_nodes(_N)=46

    线上场景下我们只需要保证：
    - 图类型/etype 名称一致（避免模型 forward 里按 etype 取边）
    - 节点数与 feats 的 N 对齐
    """
    import dgl
    import torch

    n = int(num_nodes)
    if n <= 0:
        raise ValueError(f"num_nodes must be >0, got {num_nodes}")
    # 稀疏连边：自环 + ring，避免全连接过大
    src = torch.arange(n, dtype=torch.int64)
    dst = torch.arange(n, dtype=torch.int64)
    src2 = torch.arange(n, dtype=torch.int64)
    dst2 = (torch.arange(n, dtype=torch.int64) + 1) % n
    src_all = torch.cat([src, src2], dim=0)
    dst_all = torch.cat([dst, dst2], dim=0)
    g = dgl.heterograph({("_N", "_E", "_N"): (src_all, dst_all)}, num_nodes_dict={"_N": n})
    return g


def load_art_templates(
    dataset: str,
    art_root: Optional[str] = None,
) -> dict[str, Any]:
    """
    Load node_dict, channel_dict, and a reusable graph template from ART dataset.
    """
    art_root = art_root or _default_art_root()
    base = os.path.join(art_root, "data", dataset)
    hash_dir = os.path.join(base, "hash_info")
    sample_dir = os.path.join(base, "samples")

    node_hash = _load_pkl(os.path.join(hash_dir, "node_hash.pkl"))
    node_dict = list(node_hash)
    channel_dict = _load_pkl(os.path.join(hash_dir, "channel_dict.pkl"))

    # Graph is identical across timestamps; reuse the first one.
    train_samples = _load_pkl(os.path.join(sample_dir, "train_samples.pkl"))
    graph_template = train_samples[0][1]

    return {
        "node_dict": node_dict,
        "channel_dict": channel_dict,
        "graph_template": graph_template,
    }


def infer_trace_log_channels(channel_dict: Sequence[str]) -> tuple[list[str], list[str]]:
    """
    Infer trace/log feature channel names by heuristics.

    In D1 hash_info, trace channels are:
      duration, request_count, rpc_9, rpc_1, http_13, rpc_2, rpc_4, rpc_14, rpc_13
    log channels are:
      severity_info, severity_debug, severity_error, severity_warning,
      stack_trace, transport_closing_warning, connection_error_warning,
      post_get_info, service_log_other, envoy_log_other, at
    """
    trace_names = {
        "duration",
        "request_count",
        "rpc_9",
        "rpc_1",
        "http_13",
        "rpc_2",
        "rpc_4",
        "rpc_14",
        "rpc_13",
    }
    log_names = {
        "severity_info",
        "severity_debug",
        "severity_error",
        "severity_warning",
        "stack_trace",
        "transport_closing_warning",
        "connection_error_warning",
        "post_get_info",
        "service_log_other",
        "envoy_log_other",
        "at",
    }

    trace_channels = [c for c in channel_dict if c in trace_names]
    log_channels = [c for c in channel_dict if c in log_names]
    return trace_channels, log_channels


def bucket_time(ts: int, bucket_seconds: int) -> int:
    return int((ts // bucket_seconds) * bucket_seconds)


def _to_float_or_nan(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return float("nan")


def _safe_mean(values: Sequence[Any]) -> float:
    xs = [_to_float_or_nan(x) for x in values]
    xs = [x for x in xs if x == x]  # keep non-nan
    if not xs:
        return 0.0
    return float(sum(xs) / len(xs))


def aggregate_metrics_to_channels(
    metric_long_df: pd.DataFrame,
    bucket_seconds: int,
    timestamps: Sequence[int],
    node_dict: Sequence[str],
    channel_dict: Sequence[str],
    trace_channels: Sequence[str],
    log_channels: Sequence[str],
    unknown_metric_strategy: str = "drop",
    hash_seed: int = 0,
    instance_col: str = "instance",
    metric_col: str = "metric",
    value_col: str = "value",
    time_col: str = "time",
) -> dict[int, pd.DataFrame]:
    """
    Aggregate metrics by (bucket_time, instance, metric_name)->mean(value),
    then map metric_name to channel_dict indices for metric channels.
    """
    metric_channels = [c for c in channel_dict if c not in set(trace_channels) and c not in set(log_channels)]
    metric_channel_set = set(metric_channels)

    if metric_long_df is None or metric_long_df.empty:
        # Return zero matrices for all timestamps
        return {ts: pd.DataFrame(0.0, index=node_dict, columns=metric_channels) for ts in timestamps}

    df = metric_long_df.copy()
    if time_col not in df.columns or instance_col not in df.columns or metric_col not in df.columns or value_col not in df.columns:
        raise ValueError(f"metric_long_df missing required columns. Need: {time_col},{instance_col},{metric_col},{value_col}")

    df["bucket_time"] = df[time_col].astype(int).apply(lambda x: bucket_time(x, bucket_seconds))
    df = df[df["bucket_time"].isin(set(map(int, timestamps)))]

    # mean per (bucket_time, instance, metric)
    grp = df.groupby(["bucket_time", instance_col, metric_col])[value_col].mean().reset_index()
    pivoted: dict[int, pd.DataFrame] = {}

    def _hash_to_channel(name: str) -> str:
        # Stable hashing to map unknown metrics into known metric channels
        import hashlib

        h = hashlib.md5((str(hash_seed) + "::" + name).encode("utf-8")).hexdigest()
        idx = int(h[:8], 16) % max(1, len(metric_channels))
        return metric_channels[idx]

    for ts in timestamps:
        sub = grp[grp["bucket_time"] == int(ts)]
        mat = pd.DataFrame(0.0, index=node_dict, columns=metric_channels)
        for _, row in sub.iterrows():
            inst = str(row[instance_col])
            met = str(row[metric_col])
            if inst not in mat.index:
                continue

            if met in mat.columns:
                mat.loc[inst, met] = float(row[value_col])
                continue

            if unknown_metric_strategy == "hash":
                ch = _hash_to_channel(met)
                # aggregate collisions by sum (keeps signal non-zero)
                mat.loc[inst, ch] = float(mat.loc[inst, ch]) + float(row[value_col])
            elif unknown_metric_strategy == "drop":
                continue
            else:
                raise ValueError(f"unknown_metric_strategy must be 'drop' or 'hash', got: {unknown_metric_strategy}")
        pivoted[int(ts)] = mat
    return pivoted


def aggregate_traces_to_channels(
    trace_long_df: pd.DataFrame,
    bucket_seconds: int,
    timestamps: Sequence[int],
    node_dict: Sequence[str],
    trace_channels: Sequence[str],
    # Expected columns (best-effort)
    start_time_col: str = "start_time",
    instance_col: str = "instance",
    operation_name_col: str = "operation_name",
    duration_ms_col: str = "duration_ms",
) -> dict[int, pd.DataFrame]:
    """
    Compute trace scalar features per instance per time bucket.
    """
    if trace_long_df is None or trace_long_df.empty:
        return {ts: pd.DataFrame(0.0, index=node_dict, columns=trace_channels) for ts in timestamps}

    df = trace_long_df.copy()
    for c in [start_time_col, instance_col, operation_name_col]:
        if c not in df.columns:
            raise ValueError(f"trace_long_df missing column: {c}")

    if duration_ms_col not in df.columns:
        df[duration_ms_col] = 0.0

    df[start_time_col] = df[start_time_col].astype(int)
    df["bucket_time"] = df[start_time_col].apply(lambda x: bucket_time(x, bucket_seconds))
    df = df[df["bucket_time"].isin(set(map(int, timestamps)))]

    # Prepare result
    out: dict[int, pd.DataFrame] = {}
    for ts in timestamps:
        mat = pd.DataFrame(0.0, index=node_dict, columns=trace_channels)
        sub = df[df["bucket_time"] == int(ts)]
        if sub.empty:
            out[int(ts)] = mat
            continue

        # Duration and request_count use simple stats over spans
        for inst in node_dict:
            s_inst = sub[sub[instance_col].astype(str) == str(inst)]
            if s_inst.empty:
                continue
            if "duration" in mat.columns:
                mat.loc[inst, "duration"] = _safe_mean(s_inst[duration_ms_col].tolist())
            if "request_count" in mat.columns:
                mat.loc[inst, "request_count"] = float(len(s_inst))

            # rpc_x and http_*
            op_series = s_inst[operation_name_col].fillna("").astype(str)
            for ch in trace_channels:
                if ch in ("duration", "request_count"):
                    continue
                # channel like "rpc_9", "http_13"
                mask = op_series.str.contains(re.escape(str(ch)), regex=True)
                mat.loc[inst, ch] = float(mask.sum())
        out[int(ts)] = mat
    return out


def aggregate_logs_to_channels(
    log_long_df: pd.DataFrame,
    bucket_seconds: int,
    timestamps: Sequence[int],
    node_dict: Sequence[str],
    log_channels: Sequence[str],
    time_col: str = "time",
    instance_col: str = "instance",
    level_col: str = "level",
    message_col: str = "message",
    source_col: str = "source",
) -> dict[int, pd.DataFrame]:
    """
    Compute log scalar features per instance per time bucket using heuristics.
    """
    if log_long_df is None or log_long_df.empty:
        return {ts: pd.DataFrame(0.0, index=node_dict, columns=log_channels) for ts in timestamps}

    df = log_long_df.copy()
    for c in [time_col, instance_col, message_col]:
        if c not in df.columns:
            raise ValueError(f"log_long_df missing column: {c}")
    if level_col not in df.columns:
        df[level_col] = None
    if source_col not in df.columns:
        df[source_col] = None

    df[time_col] = df[time_col].astype(int)
    df["bucket_time"] = df[time_col].apply(lambda x: bucket_time(x, bucket_seconds))
    df = df[df["bucket_time"].isin(set(map(int, timestamps)))]

    out: dict[int, pd.DataFrame] = {}
    for ts in timestamps:
        mat = pd.DataFrame(0.0, index=node_dict, columns=log_channels)
        sub = df[df["bucket_time"] == int(ts)]
        if sub.empty:
            out[int(ts)] = mat
            continue

        for inst in node_dict:
            s_inst = sub[sub[instance_col].astype(str) == str(inst)]
            if s_inst.empty:
                continue

            msg = s_inst[message_col].fillna("").astype(str)
            level = s_inst[level_col].fillna("").astype(str) if level_col in s_inst.columns else pd.Series([""] * len(s_inst), index=s_inst.index)
            source = s_inst[source_col].fillna("").astype(str) if source_col in s_inst.columns else pd.Series([""] * len(s_inst), index=s_inst.index)

            # severity_*
            for sev in ["info", "debug", "error", "warning"]:
                ch = f"severity_{sev}"
                if ch in mat.columns:
                    mask = level.str.lower().eq(sev)
                    mat.loc[inst, ch] = float(mask.sum())

            if "stack_trace" in mat.columns:
                mat.loc[inst, "stack_trace"] = float(msg.str.contains("stack", case=False).sum())

            if "transport_closing_warning" in mat.columns:
                mask_transport = msg.str.contains("transport", case=False, na=False)
                mask_closing = msg.str.contains("closing", case=False, na=False)
                mat.loc[inst, "transport_closing_warning"] = float((mask_transport & mask_closing).sum())

            if "connection_error_warning" in mat.columns:
                mask_conn = msg.str.contains("connection", case=False, na=False)
                mask_err = msg.str.contains("error", case=False, na=False)
                mat.loc[inst, "connection_error_warning"] = float((mask_conn & mask_err).sum())

            if "post_get_info" in mat.columns:
                mat.loc[inst, "post_get_info"] = float(msg.str.contains("post_get", case=False).sum())

            if "service_log_other" in mat.columns:
                mat.loc[inst, "service_log_other"] = float(source.str.contains("service", case=False).sum())

            if "envoy_log_other" in mat.columns:
                mat.loc[inst, "envoy_log_other"] = float(source.str.contains("envoy", case=False).sum())

            if "at" in mat.columns:
                # Common pattern in stack trace lines: "at com.xxx..."
                mat.loc[inst, "at"] = float(msg.str.contains(r"\bat\b", case=False, regex=True).sum())

        out[int(ts)] = mat

    return out


def build_art_feats_matrix(
    metric_mat: pd.DataFrame,
    trace_mat: pd.DataFrame,
    log_mat: pd.DataFrame,
    node_dict: Sequence[str],
    channel_dict: Sequence[str],
    trace_channels: Sequence[str],
    log_channels: Sequence[str],
) -> "torch.Tensor":
    """
    Build feats[N, 130] tensor in the order of channel_dict.
    """
    import torch

    N = len(node_dict)
    C = len(channel_dict)
    feats = torch.zeros((N, C), dtype=torch.float32)

    idx_node = {n: i for i, n in enumerate(node_dict)}
    trace_set = set(trace_channels)
    log_set = set(log_channels)

    # Fill metrics
    for ch in channel_dict:
        col_i = channel_dict.index(ch)
        if ch in trace_set:
            src = trace_mat
        elif ch in log_set:
            src = log_mat
        else:
            src = metric_mat

        if ch not in src.columns:
            continue
        for node in node_dict:
            feats[idx_node[node], col_i] = float(src.loc[node, ch])

    return feats


def build_art_samples_from_long_modalities(
    dataset: str,
    metric_long_df: pd.DataFrame,
    log_long_df: pd.DataFrame,
    trace_long_df: pd.DataFrame,
    timestamps: Sequence[int],
    bucket_seconds: int = 60,
    split_ratio: float = 0.6,
    art_root: Optional[str] = None,
    graph_template: Any = None,
    node_dict_override: Optional[Sequence[str]] = None,
    channel_dict_override: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """
    Main conversion entry:
    - bucket and aggregate metric/log/trace long tables
    - build feats per timestamp
    - split into train_samples/test_samples by `split_ratio` on timestamps
    """
    import torch
    import dgl

    templates = load_art_templates(dataset=dataset, art_root=art_root)
    node_dict = list(node_dict_override) if node_dict_override is not None else templates["node_dict"]
    channel_dict = list(channel_dict_override) if channel_dict_override is not None else templates["channel_dict"]
    graph_template = graph_template or templates["graph_template"]

    trace_channels, log_channels = infer_trace_log_channels(channel_dict)
    timestamps_sorted = sorted(map(int, timestamps))
    if not timestamps_sorted:
        raise ValueError("timestamps is empty")

    # Aggregate per bucket timestamp
    metric_bucket_mats = aggregate_metrics_to_channels(
        metric_long_df=metric_long_df,
        bucket_seconds=bucket_seconds,
        timestamps=timestamps_sorted,
        node_dict=node_dict,
        channel_dict=channel_dict,
        trace_channels=trace_channels,
        log_channels=log_channels,
        unknown_metric_strategy="hash",
        hash_seed=0,
    )
    trace_bucket_mats = aggregate_traces_to_channels(
        trace_long_df=trace_long_df,
        bucket_seconds=bucket_seconds,
        timestamps=timestamps_sorted,
        node_dict=node_dict,
        trace_channels=trace_channels,
    )
    log_bucket_mats = aggregate_logs_to_channels(
        log_long_df=log_long_df,
        bucket_seconds=bucket_seconds,
        timestamps=timestamps_sorted,
        node_dict=node_dict,
        log_channels=log_channels,
    )

    samples = []
    for ts in timestamps_sorted:
        metric_mat = metric_bucket_mats[int(ts)]
        trace_mat = trace_bucket_mats[int(ts)]
        log_mat = log_bucket_mats[int(ts)]

        feats = build_art_feats_matrix(
            metric_mat=metric_mat,
            trace_mat=trace_mat,
            log_mat=log_mat,
            node_dict=node_dict,
            channel_dict=channel_dict,
            trace_channels=trace_channels,
            log_channels=log_channels,
        )
        # ART expects (timestamp, graph, feats)
        samples.append((int(ts), graph_template, feats))

    # Split by split_ratio over timestamps
    split_ts = timestamps_sorted[int(len(timestamps_sorted) * split_ratio)]
    train_samples = [s for s in samples if s[0] <= split_ts]
    test_samples = [s for s in samples if s[0] > split_ts]

    return {
        "train_samples": train_samples,
        "test_samples": test_samples,
        "node_dict": node_dict,
        "channel_dict": channel_dict,
        "trace_channels": trace_channels,
        "log_channels": log_channels,
    }


def export_train_test_samples(
    train_samples: list,
    test_samples: list,
    sample_dir: str,
):
    """
    Export to ART sample_dir structure:
        sample_dir/train_samples.pkl
        sample_dir/test_samples.pkl
    """
    os.makedirs(sample_dir, exist_ok=True)
    with open(os.path.join(sample_dir, "train_samples.pkl"), "wb") as f:
        pickle.dump(train_samples, f)
    with open(os.path.join(sample_dir, "test_samples.pkl"), "wb") as f:
        pickle.dump(test_samples, f)

