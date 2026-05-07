# -*- coding: utf-8 -*-
"""Fault dataset collection for SFT/RL training data."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from .data_source_registry import get_adapter, inject_fault_on_platform
from .skill_hermes_aiops import SkillHermesAIOpsHarness


DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "fault_collections"


class FaultDatasetCollector:
    """One-click continuous fault-injection dataset collector."""

    DEFAULT_PLATFORMS = ["sock-shop", "online-shopping", "train-ticket"]

    def __init__(self, data_dir: str | Path = DATA_DIR) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.harness = SkillHermesAIOpsHarness()

    def start_collection(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        platforms = spec.get("platforms") or self.DEFAULT_PLATFORMS
        platforms = [p for p in platforms if p in self.DEFAULT_PLATFORMS]
        if not platforms:
            platforms = self.DEFAULT_PLATFORMS[:]
        format_type = str(spec.get("format_type") or "alpaca_sft")
        rounds = max(1, min(int(spec.get("rounds_per_platform") or 1), 20))
        duration = max(10, int(spec.get("duration_seconds") or 120))
        observation = max(duration, int(spec.get("observation_window_seconds") or duration + 120))
        interval = max(1, int(spec.get("collection_interval_seconds") or 15))
        custom_template = str(spec.get("custom_template") or "").strip()

        session_id = f"fault-dataset-{uuid.uuid4().hex[:10]}"
        samples: List[Dict[str, Any]] = []
        events: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []

        for platform in platforms:
            try:
                adapter = get_adapter(platform)
                faults = adapter.list_faults()
                services = self._load_services(adapter, faults)
                for idx in range(rounds):
                    fault = faults[idx % len(faults)] if faults else {"fault_type": "pod_crash", "case_id": "fault-pod_crash"}
                    target = services[(idx * 3) % len(services)] if services else "unknown"
                    fault_type = fault.get("fault_type") or str(fault.get("case_id", "fault-pod_crash")).replace("fault-", "", 1)
                    scheduled_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")
                    result = inject_fault_on_platform(
                        platform,
                        str(fault_type),
                        str(target),
                        scheduled_at=scheduled_at,
                        duration_seconds=duration,
                        observation_window_seconds=observation,
                        collection_interval_seconds=interval,
                        injection_mode="live_kubernetes_required",
                    )
                    detail = adapter.get_case_detail(result["case_id"])
                    sample = self._format_sample(
                        detail=detail,
                        platform=platform,
                        format_type=format_type,
                        custom_template=custom_template,
                    )
                    samples.append(sample)
                    events.append(
                        {
                            "platform": platform,
                            "case_id": result.get("case_id"),
                            "fault_type": fault_type,
                            "target": target,
                            "status": result.get("status"),
                            "injection": result.get("fault_injection", {}),
                        }
                    )
            except Exception as exc:
                errors.append({"platform": platform, "error": str(exc)})

        jsonl_path = self.data_dir / f"{session_id}.jsonl"
        summary_path = self.data_dir / f"{session_id}.json"
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for sample in samples:
                f.write(json.dumps(sample, ensure_ascii=False) + "\n")

        summary = {
            "session_id": session_id,
            "status": "completed" if samples else "error",
            "format_type": format_type,
            "platforms": platforms,
            "sample_count": len(samples),
            "events": events,
            "errors": errors,
            "jsonl_path": str(jsonl_path),
            "summary_path": str(summary_path),
            "harness_policy": self.harness.build_collection_policy(platforms, format_type),
            "preview": samples[:3],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        return summary

    def list_sessions(self) -> List[Dict[str, Any]]:
        sessions = []
        for path in sorted(self.data_dir.glob("fault-dataset-*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                sessions.append(
                    {
                        "session_id": data.get("session_id"),
                        "status": data.get("status"),
                        "format_type": data.get("format_type"),
                        "sample_count": data.get("sample_count"),
                        "created_at": data.get("created_at"),
                        "summary_path": str(path),
                    }
                )
            except (OSError, json.JSONDecodeError):
                continue
        return sessions[:40]

    def get_session(self, session_id: str) -> Dict[str, Any]:
        path = self.data_dir / f"{session_id}.json"
        if not path.exists():
            raise FileNotFoundError(session_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_services(self, adapter: Any, faults: List[Dict[str, Any]]) -> List[str]:
        if hasattr(adapter, "SERVICES"):
            return list(getattr(adapter, "SERVICES") or [])
        try:
            module = __import__(adapter.__module__, fromlist=["SERVICES"])
            module_services = getattr(module, "SERVICES", [])
            if module_services:
                return list(module_services)
        except Exception:
            pass
        if faults:
            detail = adapter.get_case_detail(faults[0].get("case_id", "fault-pod_crash"))
            return list((detail.get("service_graph") or {}).get("services") or detail.get("service_inventory") or [])
        return []

    def _format_sample(
        self,
        *,
        detail: Dict[str, Any],
        platform: str,
        format_type: str,
        custom_template: str,
    ) -> Dict[str, Any]:
        evidence = self._summarize_evidence(detail)
        answer = detail.get("root_cause_ground_truth") or "unknown"
        base_meta = {
            "platform": platform,
            "case_id": detail.get("case_id"),
            "case_name": detail.get("case_name"),
            "fault_injection": detail.get("fault_injection", {}),
            "source": detail.get("source"),
            "severity": detail.get("severity"),
        }
        if format_type == "rl":
            return {
                "prompt": self._build_prompt(detail, evidence),
                "chosen": self._build_rca_answer(answer, evidence),
                "rejected": "仅按异常数量最高的服务作为根因，未区分传播症状和直接根因证据。",
                "reward_model": {
                    "target": answer,
                    "positive_reward": 1.0,
                    "negative_reward": -0.5,
                    "judge_dimensions": ["root_cause_correctness", "evidence_citation", "victim_vs_root_cause"],
                },
                "trajectory": [
                    {"agent": "sop_agent", "artifact": "fault_injection_window"},
                    {"agent": "context_prompt_agent", "artifact": "multimodal_context_contract"},
                    {"agent": "tool_decision_agent", "artifact": "adaptive_tool_plan"},
                    {"agent": "diagnosis_agent", "artifact": "ranked_rca"},
                ],
                "metadata": base_meta,
            }
        if format_type == "custom" and custom_template:
            return {
                "custom_template": custom_template,
                "variables": {
                    "instruction": "基于故障注入数据执行 AIOps RCA",
                    "input": evidence,
                    "output": self._build_rca_answer(answer, evidence),
                    "metadata": base_meta,
                },
                "metadata": base_meta,
            }
        return {
            "instruction": "你是 AIOps 根因定位 Agent。请基于故障注入后的 log、trace、metric、alert 和拓扑信息，给出根因服务、证据和不确定性。",
            "input": json.dumps(evidence, ensure_ascii=False),
            "output": self._build_rca_answer(answer, evidence),
            "metadata": base_meta,
        }

    def _summarize_evidence(self, detail: Dict[str, Any]) -> Dict[str, Any]:
        metrics = detail.get("metrics") or {}
        logs = detail.get("logs") or {}
        traces = detail.get("traces") or {}
        alerts = detail.get("alerts") or {}
        graph = detail.get("service_graph") or {}
        return {
            "fault_injection": detail.get("fault_injection", {}),
            "metrics": (metrics.get("series_summary") or metrics.get("raw_series") or [])[:8],
            "logs": (logs.get("entries") or [])[:8],
            "traces": (traces.get("spans") or traces.get("traces") or [])[:8],
            "alerts": (alerts.get("alerts") or [])[:6],
            "topology": {
                "services": (graph.get("services") or [])[:30],
                "edges": (graph.get("edges") or [])[:40],
            },
        }

    def _build_prompt(self, detail: Dict[str, Any], evidence: Dict[str, Any]) -> str:
        return (
            f"Case {detail.get('case_id')} from {detail.get('source')} requires RCA. "
            f"Use multimodal evidence and avoid confusing propagated victims with root cause. "
            f"Evidence: {json.dumps(evidence, ensure_ascii=False)[:2500]}"
        )

    def _build_rca_answer(self, answer: str, evidence: Dict[str, Any]) -> str:
        return json.dumps(
            {
                "root_cause": answer,
                "evidence_used": {
                    "metrics": len(evidence.get("metrics") or []),
                    "logs": len(evidence.get("logs") or []),
                    "traces": len(evidence.get("traces") or []),
                    "alerts": len(evidence.get("alerts") or []),
                },
                "reasoning_policy": "先看注入时间窗和传播拓扑，再用多模态证据确认根因与受害服务差异。",
            },
            ensure_ascii=False,
        )
