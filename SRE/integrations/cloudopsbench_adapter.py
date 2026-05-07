"""Cloud-OpsBench snapshot adapter for the unified Ops Factory dashboard."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from .base_data_source import BaseDataSource, DataSourceError

logger = logging.getLogger(__name__)

KNOWN_METRIC_SUFFIXES = (
    "cpu",
    "mem",
    "cpu_cfs",
    "rps",
    "success_rate",
    "network_receive",
    "network_transmit",
    "p50latency",
    "p90latency",
)


@dataclass(frozen=True)
class CaseRef:
    system: str
    fault_category: str
    case_id: str

    @property
    def ref(self) -> str:
        return f"{self.system}/{self.fault_category}/{self.case_id}"


class CloudOpsBenchAdapter(BaseDataSource):
    """Read Cloud-OpsBench cases and expose a dashboard-friendly representation."""

    @property
    def name(self) -> str:
        return "Cloud-OpsBench"

    @property
    def source_type(self) -> str:
        return "static"

    @property
    def description(self) -> str:
        return "Static benchmark dataset — pre-collected fault cases with ground truth for evaluation. No live fault injection."

    def __init__(self, root_dir: Optional[str] = None):
        default_root = Path(__file__).resolve().parents[2] / "Cloud-OpsBench"
        self.root_dir = Path(root_dir or default_root).expanduser().resolve()
        self.benchmark_dir = self.root_dir / "benchmark"
        self.trajectory_dir = self.root_dir / "golden-trajectory"
        if not self.benchmark_dir.exists():
            raise FileNotFoundError(f"Cloud-OpsBench benchmark dir not found: {self.benchmark_dir}")

    def get_platform_summary(self) -> Dict[str, Any]:
        cases = self.list_cases(limit=None)
        systems = sorted({case["system"] for case in cases})
        categories = sorted({case["fault_category"] for case in cases})
        return {
            "root_dir": str(self.root_dir),
            "system_count": len(systems),
            "fault_category_count": len(categories),
            "case_count": len(cases),
            "systems": systems,
            "fault_categories": categories,
            "continuous_injection_supported": False,
            "injection_mode": "snapshot_replay",
            "injection_message": (
                "Cloud-OpsBench 当前发布版基于静态快照回放，不会持续在线注入新故障。"
            ),
        }

    # ── BaseDataSource interface ────────────────────────────────────
    def list_faults(self) -> List[Dict[str, Any]]:
        """List all static cases as fault entries."""
        cases = self.list_cases(limit=None)
        return [
            {
                "case_id": c["ref"],
                "case_name": f"{c['fault_category']} / {c['root_cause']}",
                "timestamp": "",
                "severity": "critical" if c.get("fault_taxonomy") else "info",
                "system": c["system"],
                "fault_category": c["fault_category"],
            }
            for c in cases
        ]

    def inject_fault(self, fault_type: str, target: str, **kwargs) -> Dict[str, Any]:
        """Static source does not support fault injection."""
        raise DataSourceError("Cloud-OpsBench is a static snapshot dataset and does not support live fault injection.")

    def list_cases(
        self,
        system: str = "",
        fault_category: str = "",
        search: str = "",
        limit: Optional[int] = 200,
    ) -> List[Dict[str, Any]]:
        search_text = str(search or "").strip().lower()
        rows: List[Dict[str, Any]] = []

        for case_ref, case_dir in self._iter_case_dirs():
            if system and case_ref.system != system:
                continue
            if fault_category and case_ref.fault_category != fault_category:
                continue

            metadata = self._read_json(case_dir / "metadata.json", default={})
            case_row = self._build_case_row(case_ref, case_dir, metadata)

            haystack = " ".join(
                [
                    case_row["system"],
                    case_row["fault_category"],
                    case_row["query"],
                    case_row["namespace"],
                    case_row["fault_taxonomy"],
                    case_row["fault_object"],
                    case_row["root_cause"],
                ]
            ).lower()
            if search_text and search_text not in haystack:
                continue

            rows.append(case_row)

        rows.sort(key=lambda item: (item["system"], item["fault_category"], item["case_id"]))
        return rows if limit is None else rows[:limit]

    def get_case_detail(self, case_ref: str) -> Dict[str, Any]:
        ref = self._parse_case_ref(case_ref)
        case_dir = self._case_dir(ref)
        metadata = self._read_json(case_dir / "metadata.json", default={})
        raw_data_dir = case_dir / "raw_data"
        tool_cache = self._read_json(case_dir / "tool_cache.json", default={})

        logs_payload, service_inventory = self._parse_logs(raw_data_dir / "logs.json")
        alerts_payload = self._read_json(raw_data_dir / "alert.json", default={})
        metrics_payload = self._parse_metrics(raw_data_dir / "metrics.csv")
        k8s_payload = self._parse_k8s_states(raw_data_dir / "k8s_states.json")
        trajectories = self._load_trajectories(ref)
        service_graph = self._parse_service_dependencies(tool_cache)

        service_names = sorted(
            set(service_inventory)
            | set(metrics_payload["services"])
            | set(service_graph["services"])
        )

        return {
            "ref": ref.ref,
            "system": ref.system,
            "fault_category": ref.fault_category,
            "case_id": ref.case_id,
            "namespace": metadata.get("namespace", ""),
            "query": metadata.get("query", ""),
            "result": metadata.get("result", {}),
            "root_cause_ground_truth": self._format_ground_truth(metadata.get("result", {})),
            "process": metadata.get("process", {}),
            "platform": {
                "mode": "snapshot_replay",
                "continuous_injection_supported": False,
                "continuous_injection_message": (
                    "当前是离线快照回放模式，案例可重复浏览和诊断，但不会自动持续注入新故障。"
                ),
            },
            "data_availability": {
                "alerts": bool(alerts_payload),
                "logs": logs_payload["total_entries"] > 0,
                "metrics": metrics_payload["row_count"] > 0,
                "k8s_states": k8s_payload["command_count"] > 0,
                "trajectories": len(trajectories) > 0,
            },
            "service_inventory": service_names,
            "metric_inventory": metrics_payload["metric_names"],
            "metric_columns": metrics_payload["columns"],
            "alerts": alerts_payload,
            "logs": logs_payload,
            "metrics": metrics_payload,
            "k8s_states": k8s_payload,
            "golden_trajectories": trajectories,
            "service_graph": service_graph,
            "tool_cache_preview": self._preview_tool_cache(tool_cache),
            "replay_steps": self._build_replay_steps(metadata, alerts_payload, logs_payload, metrics_payload, k8s_payload),
        }

    def _format_ground_truth(self, result: Dict[str, Any]) -> str:
        fault_object = str(result.get("fault_object") or result.get("root_cause") or "").strip()
        root_cause = str(result.get("root_cause") or "").strip()
        taxonomy = str(result.get("fault_taxonomy") or "").strip()
        if fault_object:
            detail = root_cause or taxonomy or "Cloud-OpsBench labeled fault"
            return f"{fault_object} is the root cause — {detail}."
        if root_cause:
            return f"{root_cause} is the root cause."
        return ""

    def build_case_context_text(self, case_ref: str) -> str:
        detail = self.get_case_detail(case_ref)
        result = detail.get("result", {})
        metric_names = ", ".join(detail.get("metric_inventory", [])[:12]) or "none"
        services = ", ".join(detail.get("service_inventory", [])[:12]) or "none"
        return (
            f"Cloud-OpsBench case={detail['ref']}\n"
            f"system={detail['system']}, category={detail['fault_category']}, namespace={detail['namespace']}\n"
            f"user symptom={detail['query']}\n"
            f"ground truth taxonomy={result.get('fault_taxonomy', '')}, object={result.get('fault_object', '')}, root cause={result.get('root_cause', '')}\n"
            f"services={services}\n"
            f"metrics={metric_names}\n"
            f"injection_mode=snapshot_replay, continuous_injection_supported=false"
        )

    def _iter_case_dirs(self) -> Iterable[Tuple[CaseRef, Path]]:
        for system_dir in sorted(path for path in self.benchmark_dir.iterdir() if path.is_dir()):
            system = "train-ticket" if system_dir.name == "trainticket" else system_dir.name
            for category_dir in sorted(path for path in system_dir.iterdir() if path.is_dir()):
                for case_dir in sorted(path for path in category_dir.iterdir() if path.is_dir()):
                    yield CaseRef(system=system, fault_category=category_dir.name, case_id=case_dir.name), case_dir

    def _build_case_row(self, case_ref: CaseRef, case_dir: Path, metadata: Dict[str, Any]) -> Dict[str, Any]:
        raw_data_dir = case_dir / "raw_data"
        result = metadata.get("result", {})
        return {
            "ref": case_ref.ref,
            "system": case_ref.system,
            "fault_category": case_ref.fault_category,
            "case_id": case_ref.case_id,
            "namespace": metadata.get("namespace", ""),
            "query": metadata.get("query", ""),
            "fault_taxonomy": result.get("fault_taxonomy", ""),
            "fault_object": result.get("fault_object", ""),
            "root_cause": result.get("root_cause", ""),
            "has_metrics": (raw_data_dir / "metrics.csv").exists(),
            "has_logs": (raw_data_dir / "logs.json").exists(),
            "has_alerts": (raw_data_dir / "alert.json").exists(),
            "has_k8s_states": (raw_data_dir / "k8s_states.json").exists(),
            "has_trajectories": self._trajectory_dir(case_ref).exists(),
        }

    def _parse_case_ref(self, case_ref: str) -> CaseRef:
        raw = str(case_ref or "").strip().strip("/")
        parts = raw.split("/")
        if len(parts) != 3:
            raise ValueError(f"Invalid case ref: {case_ref}")
        return CaseRef(system=parts[0], fault_category=parts[1], case_id=parts[2])

    def _case_dir(self, case_ref: CaseRef) -> Path:
        system_dir = "trainticket" if case_ref.system == "train-ticket" else case_ref.system
        path = self.benchmark_dir / system_dir / case_ref.fault_category / case_ref.case_id
        if not path.exists():
            raise FileNotFoundError(f"Case not found: {case_ref.ref}")
        return path

    def _trajectory_dir(self, case_ref: CaseRef) -> Path:
        system_dir = "trainticket" if case_ref.system == "train-ticket" else case_ref.system
        return self.trajectory_dir / system_dir / case_ref.fault_category / case_ref.case_id

    def _load_trajectories(self, case_ref: CaseRef) -> List[Dict[str, Any]]:
        path = self._trajectory_dir(case_ref)
        if not path.exists():
            return []
        items = []
        for json_file in sorted(path.glob("*.json")):
            payload = self._read_json(json_file, default=[])
            items.append({"name": json_file.name, "steps": payload})
        return items

    def _parse_logs(self, path: Path) -> Tuple[Dict[str, Any], List[str]]:
        raw = self._read_json(path, default={})
        services: List[str] = []
        entries: List[Dict[str, Any]] = []
        if not isinstance(raw, dict):
            return {"services": [], "entries": [], "total_entries": 0}, services

        for service, records in raw.items():
            services.append(service)
            for record in records[:200]:
                parsed = self._parse_log_line(record)
                parsed["service"] = service
                entries.append(parsed)

        entries.sort(key=lambda item: item.get("timestamp", ""))
        return {
            "services": sorted(set(services)),
            "entries": entries[:500],
            "total_entries": sum(len(records) for records in raw.values() if isinstance(records, list)),
            "error_count": sum(1 for entry in entries if str(entry.get("level", "")).lower() == "error"),
            "warn_count": sum(1 for entry in entries if str(entry.get("level", "")).lower() in {"warn", "warning"}),
        }, services

    def _parse_log_line(self, record: Any) -> Dict[str, Any]:
        if isinstance(record, dict):
            payload = record
        else:
            text = str(record)
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = {"message": text}
        return {
            "timestamp": str(payload.get("time") or payload.get("@timestamp") or ""),
            "level": payload.get("level") or payload.get("severity") or "info",
            "message": payload.get("message") or payload.get("msg") or str(payload)[:300],
            "logger": payload.get("loggerName") or payload.get("logger") or "",
            "thread": payload.get("thread") or "",
        }

    def _parse_metrics(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {
                "row_count": 0,
                "columns": [],
                "metric_names": [],
                "services": [],
                "series_summary": [],
                "sample_rows": [],
            }

        df = pd.read_csv(path)
        if "time" not in df.columns:
            df.insert(0, "time", range(len(df)))

        metric_columns = [column for column in df.columns if column != "time"]
        services = sorted({self._split_metric_column(column)[0] for column in metric_columns})
        metric_names = sorted({self._split_metric_column(column)[1] for column in metric_columns})

        series_summary = []
        for column in metric_columns[:80]:
            series = pd.to_numeric(df[column], errors="coerce").dropna()
            if series.empty:
                continue
            series_summary.append(
                {
                    "column": column,
                    "service": self._split_metric_column(column)[0],
                    "metric": self._split_metric_column(column)[1],
                    "current": round(float(series.iloc[-1]), 4),
                    "mean": round(float(series.mean()), 4),
                    "max": round(float(series.max()), 4),
                    "min": round(float(series.min()), 4),
                    "range": round(float(series.max() - series.min()), 4),
                }
            )
        series_summary.sort(key=lambda item: item["range"], reverse=True)

        sample_rows = df.head(20).to_dict(orient="records")
        return {
            "row_count": len(df),
            "columns": metric_columns,
            "metric_names": metric_names,
            "services": services,
            "series_summary": series_summary[:20],
            "sample_rows": sample_rows,
        }

    def _split_metric_column(self, column: str) -> Tuple[str, str]:
        for suffix in sorted(KNOWN_METRIC_SUFFIXES, key=len, reverse=True):
            token = f"-{suffix}"
            if column.endswith(token):
                return column[: -len(token)], suffix
        head, _, tail = column.rpartition("-")
        return (head or column, tail or "value")

    def _parse_k8s_states(self, path: Path) -> Dict[str, Any]:
        raw = self._read_json(path, default={})
        if not isinstance(raw, dict):
            return {"command_count": 0, "previews": []}
        previews = []
        for command, output in list(raw.items())[:24]:
            previews.append(
                {
                    "command": command,
                    "preview": str(output)[:1200],
                }
            )
        return {
            "command_count": len(raw),
            "previews": previews,
        }

    def _preview_tool_cache(self, tool_cache: Dict[str, Any]) -> List[Dict[str, str]]:
        if not isinstance(tool_cache, dict):
            return []
        previews = []
        for key, value in list(tool_cache.items())[:30]:
            if key == "collection_timestamp":
                continue
            previews.append({"tool": key, "preview": str(value)[:400]})
        return previews

    def _parse_service_dependencies(self, tool_cache: Dict[str, Any]) -> Dict[str, Any]:
        nodes = set()
        edges = []
        for key, value in (tool_cache or {}).items():
            if not str(key).startswith("GetServiceDependencies"):
                continue
            match = re.search(r'"service_name":"([^"]+)"', str(key))
            service = match.group(1) if match else ""
            if not service:
                continue
            nodes.add(service)
            text = str(value)
            upstream_match = re.search(r"\[Upstream \(Called By\)\]:\s*(.*)", text)
            downstream_match = re.search(r"\[Downstream \(Calls\)\]:\s*([\s\S]*)", text)
            if upstream_match:
                upstream_services = self._split_dependency_list(upstream_match.group(1))
                for upstream in upstream_services:
                    nodes.add(upstream)
                    edges.append({"source": upstream, "target": service, "type": "upstream"})
            if downstream_match:
                downstream_services = self._extract_tree_services(downstream_match.group(1))
                for downstream in downstream_services:
                    nodes.add(downstream)
                    edges.append({"source": service, "target": downstream, "type": "downstream"})
        return {
            "services": sorted(nodes),
            "edges": edges[:120],
        }

    def _split_dependency_list(self, text: str) -> List[str]:
        cleaned = text.strip()
        if not cleaned or "Leaf Node" in cleaned:
            return []
        return [item.strip() for item in cleaned.split(",") if item.strip()]

    def _extract_tree_services(self, text: str) -> List[str]:
        services = []
        for raw_line in text.splitlines():
            line = raw_line.strip(" \t├└─")
            if not line or "Leaf Node" in line:
                continue
            services.append(line.strip())
        return services

    def _build_replay_steps(
        self,
        metadata: Dict[str, Any],
        alerts_payload: Dict[str, Any],
        logs_payload: Dict[str, Any],
        metrics_payload: Dict[str, Any],
        k8s_payload: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        process = metadata.get("process", {})
        steps = [
            {
                "title": "选择故障案例",
                "description": metadata.get("query", "选择 Cloud-OpsBench 案例并进入回放视图。"),
            },
            {
                "title": "加载静态快照",
                "description": "读取 alert / logs / metrics / k8s state / tool cache，准备回放数据。",
            },
        ]
        if alerts_payload:
            steps.append(
                {
                    "title": "查看异常信号",
                    "description": f"当前案例包含 {alerts_payload.get('alert_count', 0)} 条告警或异常摘要。",
                }
            )
        if logs_payload.get("total_entries"):
            steps.append(
                {
                    "title": "查看日志模态",
                    "description": f"日志快照共 {logs_payload['total_entries']} 条，覆盖 {len(logs_payload['services'])} 个服务。",
                }
            )
        if metrics_payload.get("row_count"):
            steps.append(
                {
                    "title": "查看指标模态",
                    "description": f"指标快照共 {metrics_payload['row_count']} 行，包含 {len(metrics_payload['columns'])} 条时序列。",
                }
            )
        if k8s_payload.get("command_count"):
            steps.append(
                {
                    "title": "查看 K8s 状态",
                    "description": f"Kubernetes 现场快照包含 {k8s_payload['command_count']} 条命令输出。",
                }
            )
        if process:
            steps.append(
                {
                    "title": "参考专家轨迹",
                    "description": "可对照 golden trajectory 或 metadata.process 中的专家排障路径。",
                }
            )
        return steps

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:
            logger.warning("Failed to parse JSON %s: %s", path, exc)
            return default
