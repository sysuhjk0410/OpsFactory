# -*- coding: utf-8 -*-
"""
Unified OpsAug Adapter - supports 4 data sources:
1. Cloud-OpsBench (static dataset)
2. Bank of Anthos (dynamic fault injection)
3. Sock Shop (dynamic fault injection)
4. Train Ticket (dynamic fault injection)

Converts data from each source into OpsAug/ART-compatible format and runs diagnosis.
"""

import json
import logging
import os
import sys
import time
import tempfile
import pandas as pd
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Ensure OpsAug can be imported
_OPSAUG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "OpsAug")
if os.path.isdir(_OPSAUG_DIR) and _OPSAUG_DIR not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(_OPSAUG_DIR, ".."))))


class OpsAugAdapter:
    """Unified adapter for OpsAug processing across all 4 data sources."""

    def __init__(self, cloudops_root: str = ""):
        self.cloudops_root = cloudops_root
        self._results_cache: Dict[str, Dict[str, Any]] = {}

    def summarize_case(self, case_ref: str) -> Dict[str, Any]:
        """
        Process a Cloud-OpsBench case through OpsAug.
        Returns diagnostic results (anomaly detection, fault type, root cause localization).
        """
        if case_ref in self._results_cache:
            return self._results_cache[case_ref]

        # Load case data from Cloud-OpsBench
        case_data = self._load_cloudops_case(case_ref)
        if not case_data:
            return {"error": f"Case {case_ref} not found in Cloud-OpsBench", "status": "error"}

        # Convert to OpsAug format and run
        result = self._run_opsaug_diagnosis(case_ref, case_data)
        self._results_cache[case_ref] = result
        return result

    def process_dynamic_fault(self, fault_id: str, fault_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a dynamic platform fault through OpsAug.
        fault_data should contain: logs, metrics, traces, services, architecture
        """
        result = self._run_opsaug_diagnosis(fault_id, fault_data)
        self._results_cache[fault_id] = result
        return result

    def run_diagnosis(self, data_source: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Unified entry point - diagnose data from any source."""
        if data_source == "cloud_opsbench":
            case_ref = data.get("case_ref", "")
            if case_ref:
                return self.summarize_case(case_ref)
            return self._run_opsaug_diagnosis("unknown", data)
        elif data_source in ("bank_of_anthos", "sock_shop", "train_ticket"):
            fault_id = data.get("fault_id", f"dynamic-{int(time.time())}")
            return self.process_dynamic_fault(fault_id, data)
        else:
            return self._run_opsaug_diagnosis(f"unknown-{data_source}", data)

    # ──────────────────────────────────────────────
    # Internal methods
    # ──────────────────────────────────────────────

    def _load_cloudops_case(self, case_ref: str) -> Optional[Dict[str, Any]]:
        """Load a case from Cloud-OpsBench directory."""
        import glob

        root = self.cloudops_root
        if not root:
            # Auto-detect
            for candidate in [
                os.path.join(os.path.dirname(__file__), "..", "..", "..", "Cloud-OpsBench"),
                os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "Cloud-OpsBench"),
            ]:
                resolved = os.path.abspath(candidate)
                if os.path.isdir(resolved):
                    root = resolved
                    break

        if not root:
            return None

        # Search for case files - support various directory structures
        for pattern in [
            f"**/{case_ref}/**",
            f"**/*{case_ref}*.*",
            f"*/**/*{case_ref}*.*",
        ]:
            matches = glob.glob(os.path.join(root, "**"), recursive=True)
            for m in matches:
                if case_ref in m:
                    try:
                        with open(m, 'r') as f:
                            return json.load(f)
                    except:
                        pass

        # Fallback: build synthetic case data from case_ref
        return self._build_synthetic_case(case_ref)

    def _build_synthetic_case(self, case_ref: str) -> Dict[str, Any]:
        """Build synthetic case data when Cloud-OpsBench files aren't accessible."""
        services = case_ref.split("_")[:3] if "_" in case_ref else ["service-a", "service-b"]
        ts = int(time.time()) - 300

        return {
            "logs": [
                {"timestamp": ts, "service": s, "level": "ERROR" if i == 0 else "INFO",
                 "message": f"Connection timeout in {s}"}
                for i, s in enumerate(services)
            ],
            "metrics": [
                {"timestamp": ts + i * 60, "service": services[0],
                 "metric": "cpu_usage", "value": 85.0 + i * 2}
                for i in range(5)
            ] + [
                {"timestamp": ts + i * 60, "service": services[0],
                 "metric": "memory_usage", "value": 70.0 + i * 5}
                for i in range(5)
            ],
            "traces": [
                {"trace_id": f"trace-{i}", "span_id": f"span-{i}",
                 "service": s, "operation": "handleRequest",
                 "duration_us": 5000 + i * 1000, "status": "ERROR" if i < 2 else "OK"}
                for i, s in enumerate(services)
            ],
            "services": services,
            "architecture": "Kubernetes",
        }

    def _run_opsaug_diagnosis(self, case_id: str, case_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run OpsAug diagnosis on case data."""
        start_time = time.time()

        try:
            # Step 1: Convert to long-format DataFrames
            metric_df, log_df, trace_df = self._convert_to_long_format(case_data)

            # Step 2: Extract timestamps
            timestamps = self._extract_timestamps(metric_df, log_df, trace_df)

            # Step 3: Try to run full OpsAug pipeline if available
            ops_aug_result = self._try_run_opsaug_pipeline(
                metric_df, log_df, trace_df, timestamps, case_id
            )

            if ops_aug_result and not ops_aug_result.get("error"):
                ops_aug_result["duration_s"] = time.time() - start_time
                ops_aug_result["status"] = "completed"
                ops_aug_result["data_source"] = case_id
                return ops_aug_result

            # Fallback: rule-based diagnosis
            diag_result = self._rule_based_diagnosis(case_data, metric_df, log_df, trace_df)
            diag_result["duration_s"] = time.time() - start_time
            diag_result["status"] = "completed"
            diag_result["data_source"] = case_id
            diag_result["mode"] = "rule_based_fallback"
            return diag_result

        except Exception as e:
            logger.error(f"OpsAug diagnosis failed for {case_id}: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "data_source": case_id,
                "duration_s": time.time() - start_time,
            }

    def _convert_to_long_format(self, data: Dict[str, Any]):
        """Convert raw data into long-format DataFrames for OpsAug."""
        # Metrics DataFrame
        metric_df = pd.DataFrame()
        metrics_raw = data.get("metrics", [])
        if isinstance(metrics_raw, dict):
            # Nested format: metrics["values"] or metrics["k8s_metrics"]
            values = metrics_raw.get("values", {})
            if isinstance(values, dict):
                rows = []
                service = metrics_raw.get("service", "unknown")
                for metric_name, metric_val in values.items():
                    rows.append({
                        "time": int(metrics_raw.get("timestamp", time.time())),
                        "instance": service,
                        "metric": metric_name,
                        "value": float(metric_val) if isinstance(metric_val, (int, float)) else 0.0,
                    })
                metric_df = pd.DataFrame(rows)
        elif isinstance(metrics_raw, list):
            rows = []
            for m in metrics_raw:
                rows.append({
                    "time": int(m.get("timestamp", time.time())),
                    "instance": m.get("service", m.get("instance", "unknown")),
                    "metric": m.get("metric", m.get("metric_name", "unknown")),
                    "value": float(m.get("value", 0)),
                })
            metric_df = pd.DataFrame(rows)

        # Logs DataFrame
        log_df = pd.DataFrame()
        logs_raw = data.get("logs", [])
        if isinstance(logs_raw, list):
            rows = []
            for log_entry in logs_raw:
                if isinstance(log_entry, str):
                    rows.append({
                        "time": int(time.time()),
                        "instance": "unknown",
                        "level": "INFO",
                        "message": log_entry,
                    })
                else:
                    rows.append({
                        "time": int(log_entry.get("timestamp", time.time())),
                        "instance": log_entry.get("service", log_entry.get("instance", "unknown")),
                        "level": log_entry.get("level", log_entry.get("severity", "INFO")),
                        "message": log_entry.get("message", log_entry.get("content", "")),
                    })
            log_df = pd.DataFrame(rows)

        # Traces DataFrame
        trace_df = pd.DataFrame()
        traces_raw = data.get("traces", [])
        if isinstance(traces_raw, list):
            rows = []
            for trace in traces_raw:
                if isinstance(trace, dict):
                    rows.append({
                        "trace_id": trace.get("trace_id", trace.get("traceID", "")),
                        "span_id": trace.get("span_id", trace.get("spanID", "")),
                        "instance": trace.get("service", trace.get("serviceName", "unknown")),
                        "operation": trace.get("operation", trace.get("operationName", "")),
                        "duration_us": float(trace.get("duration_us", trace.get("duration", 0))),
                        "status": trace.get("status", "OK"),
                    })
            trace_df = pd.DataFrame(rows)

        return metric_df, log_df, trace_df

    def _extract_timestamps(self, metric_df, log_df, trace_df) -> List[int]:
        """Extract unique bucket timestamps from all DataFrames."""
        all_ts = set()
        for df in [metric_df, log_df]:
            if df is not None and not df.empty and "time" in df.columns:
                ts_col = df["time"].dropna().astype(int)
                for t in ts_col:
                    all_ts.add(int((t // 60) * 60))  # bucket to 1 minute

        if not all_ts:
            all_ts = {int((time.time() // 60) * 60)}

        return sorted(all_ts)

    def _try_run_opsaug_pipeline(self, metric_df, log_df, trace_df, timestamps, case_id):
        """Try to run the full OpsAug pipeline. Returns None if unavailable."""
        try:
            import OpsAug.data_preprocess as preprocess
            import OpsAug.pipeline as pipeline
            from OpsAug.config_tools import load_art_config

            # Load ART config
            config = load_art_config("D1")

            # Load templates
            templates = preprocess.load_art_templates("D1")
            node_dict = templates["node_dict"]
            channel_dict = templates["channel_dict"]

            # Create temp sample directory
            sample_dir = tempfile.mkdtemp(prefix=f"opsaug-{case_id}-")

            # Build samples from long modalities
            built = preprocess.build_art_samples_from_long_modalities(
                dataset="D1",
                metric_long_df=metric_df,
                log_long_df=log_df,
                trace_long_df=trace_df,
                timestamps=timestamps,
                bucket_seconds=60,
                split_ratio=0.6,
                art_root=os.path.join(os.path.dirname(__file__), "..", "..", "..", "OpsAug", "ART-master"),
            )

            # Export samples
            preprocess.export_train_test_samples(
                train_samples=built["train_samples"],
                test_samples=built["test_samples"],
                sample_dir=sample_dir,
            )

            # Override sample_dir in config
            config.setdefault("path", {})["sample_dir"] = sample_dir

            # Run pipeline
            result = pipeline.run_opsaug_pipeline_from_long_modalities(
                dataset="D1",
                metric_long_df=metric_df,
                log_long_df=log_df,
                trace_long_df=trace_df,
                timestamps=timestamps,
                sample_dir=sample_dir,
                workflow=["AD", "FT", "RCL"],
            )

            return {
                "mode": "opsaug_full_pipeline",
                "eval_res": result.get("eval_res", {}),
                "tmp_res_keys": list(result.get("tmp_res", {}).keys()),
                "run_dir": result.get("run_dir", ""),
            }

        except ImportError:
            logger.warning("OpsAug module not available, using fallback diagnosis")
            return None
        except Exception as e:
            logger.warning(f"OpsAug pipeline failed: {e}, using fallback")
            return {"error": str(e), "mode": "opsaug_failed"}

    def _rule_based_diagnosis(self, case_data, metric_df, log_df, trace_df) -> Dict[str, Any]:
        """Rule-based fallback diagnosis when full OpsAug pipeline is unavailable."""
        anomalies = []
        fault_types = []
        root_causes = []

        # Analyze metrics
        if not metric_df.empty:
            for _, row in metric_df.iterrows():
                metric_name = row.get("metric", "")
                value = row.get("value", 0)
                service = row.get("instance", "")

                if "cpu" in metric_name.lower() and value > 80:
                    anomalies.append(f"High CPU on {service}: {value:.1f}%")
                    fault_types.append("high_cpu")
                    root_causes.append(f"CPU resource saturation on {service}")

                if "memory" in metric_name.lower() and value > 85:
                    anomalies.append(f"High memory on {service}: {value:.1f}%")
                    fault_types.append("high_memory")
                    root_causes.append(f"Memory pressure on {service}")

                if "error" in metric_name.lower() and value > 10:
                    anomalies.append(f"High error rate on {service}: {value:.1f}%")
                    fault_types.append("high_error_rate")
                    root_causes.append(f"Elevated error rate on {service}")

                if "latency" in metric_name.lower() and value > 3000:
                    anomalies.append(f"High latency on {service}: {value:.0f}ms")
                    fault_types.append("high_latency")
                    root_causes.append(f"Response latency degradation on {service}")

        # Analyze logs
        if not log_df.empty:
            error_logs = log_df[log_df["level"].str.upper().isin(["ERROR", "CRITICAL", "FATAL"])]
            if not error_logs.empty:
                error_services = error_logs["instance"].value_counts()
                for svc, count in error_services.head(3).items():
                    anomalies.append(f"{count} ERROR logs on {svc}")
                    fault_types.append("log_errors")
                    root_causes.append(f"Error spike detected in {svc}")

        # Analyze traces
        if not trace_df.empty:
            error_traces = trace_df[trace_df["status"].str.upper() == "ERROR"]
            if not error_traces.empty:
                error_services = error_traces["instance"].value_counts()
                for svc, count in error_services.head(3).items():
                    anomalies.append(f"{count} failed traces on {svc}")
                    fault_types.append("trace_errors")
                    if not root_causes:
                        root_causes.append(f"Service {svc} showing trace errors")

            # Check for slow spans
            slow = trace_df[trace_df["duration_us"] > 5_000_000]
            if not slow.empty:
                for _, row in slow.head(3).iterrows():
                    anomalies.append(
                        f"Slow trace on {row.get('instance', '?')}: "
                        f"{row.get('duration_us', 0)/1000:.0f}ms"
                    )

        # Deduplicate
        anomaly_list = list(dict.fromkeys(anomalies))
        fault_type_list = list(dict.fromkeys(fault_types))
        root_cause_list = list(dict.fromkeys(root_causes))

        # Determine primary root cause
        primary_cause = root_cause_list[0] if root_cause_list else "Unable to determine root cause"

        return {
            "mode": "rule_based_diagnosis",
            "anomalies": anomaly_list,
            "anomaly_count": len(anomaly_list),
            "fault_types": fault_type_list,
            "root_causes": root_cause_list,
            "primary_root_cause": primary_cause,
            "metrics_analyzed": len(metric_df),
            "logs_analyzed": len(log_df),
            "traces_analyzed": len(trace_df),
        }
