# -*- coding: utf-8 -*-
"""Standalone Hermes + SkillClaw RCA runtime for Ops Factory.

This module intentionally sits beside the LangChain multi-agent runtime. Hermes
contributes context, memory and trajectory discipline; SkillClaw contributes
skill retrieval and failure-driven skill updates. The runtime still uses the
existing AIOps tools and LLM client, but the RCA path is exposed as an
independent window/API.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from .base_data_source import BaseDataSource
from .rca_orchestrator import RcaOrchestrator
from .self_evolution import SelfEvolution
from .skill_hermes_aiops import SkillHermesAIOpsHarness


class HermesSkillClawRCA:
    """Run RCA with a Hermes context engine and SkillClaw-style skill loop."""

    def __init__(
        self,
        data_source: BaseDataSource,
        llm_client=None,
        llm_config=None,
        llm_status: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.data_source = data_source
        self.orchestrator = RcaOrchestrator(
            data_source=data_source,
            llm_client=llm_client,
            llm_config=llm_config,
            llm_status=llm_status,
        )
        self.harness = SkillHermesAIOpsHarness()
        self.evolution = SelfEvolution()

    def run(self, case_id: str, run_tools: Optional[List[str]] = None) -> Dict[str, Any]:
        start = time.time()
        detail = self.data_source.get_case_detail(case_id)
        raw = self.orchestrator._build_raw_data_summary(detail)
        guidance = self.evolution.get_runtime_guidance(
            self.data_source.name,
            str(detail.get("case_name") or detail.get("fault_category") or ""),
        )
        selected_tools, tool_plan = self.orchestrator._select_tools(detail, run_tools, guidance)
        context = self._build_hermes_context(detail, raw, guidance, tool_plan)
        prompt_guidance = self._build_prompt_guidance(context, guidance, tool_plan)
        skill_bundle = self.harness.build_agent_adaptation(
            detail=detail,
            context=context,
            tool_plan=tool_plan,
            state=self._harness_state(guidance),
        )

        stages: List[Dict[str, Any]] = [
            self._stage(
                "hermes_context_engine",
                "Hermes 上下文引擎",
                "把故障注入契约、日志/链路/指标/告警、拓扑和成功标准整理为分层上下文。",
                "raw_fault_case",
                "hermes_context_contract",
                {
                    "budget": context.get("context_contract", {}).get("budget"),
                    "modalities": context.get("context_layers", {}).get("modalities"),
                },
            ),
            self._stage(
                "skillclaw_skill_retrieval",
                "SkillClaw 技能检索",
                "根据模态、拓扑和历史失败轨迹命中 AIOps RCA 技能，而不是固定套模板。",
                "hermes_context_contract",
                "matched_aiops_skills",
                {
                    "matched_skills": skill_bundle.get("skillclaw_layer", {}).get("matched_skills", []),
                    "repos": skill_bundle.get("repos", {}),
                },
            ),
            self._stage(
                "hermes_tool_router",
                "Hermes 工具路由",
                "读取上下文预算、记忆和 SkillClaw 技能，只选择本轮有收益的工具。",
                "matched_aiops_skills",
                "selected_tool_plan",
                {"selected_tools": selected_tools, "tool_plan": tool_plan},
            ),
        ]

        tool_outputs: Dict[str, Any] = {}
        tool_evidence: List[Dict[str, Any]] = []
        steps: Dict[str, Dict[str, Any]] = {}
        previous_artifact: Optional[Dict[str, Any]] = None
        for tool in selected_tools:
            t0 = time.time()
            try:
                output = self._run_tool(tool, case_id)
                status = "ok"
                summary = self.orchestrator._summarize_tool_result(output)
            except Exception as exc:  # tool failures are observed, not hidden
                output = {"error": str(exc)}
                status = "error"
                summary = str(exc)
            data_flow = self.orchestrator._build_tool_data_flow(
                tool, detail, output, status, previous_artifact
            )
            previous_artifact = data_flow.get("after_data") or previous_artifact
            tool_outputs[tool] = output
            if status == "ok":
                evidence = dict(output)
                evidence.setdefault("tool", tool)
                tool_evidence.append(evidence)
            step_id = tool.lower().replace(" ", "_")
            steps[step_id] = {
                "duration_s": round(time.time() - t0, 2),
                "status": status,
                "explanation": self.orchestrator._tool_explanation(tool),
                "summary": summary,
                "selection_reason": self._selection_reason(tool, tool_plan),
                "data_flow": data_flow,
            }
            stages.append(
                self._stage(
                    f"tool_{step_id}",
                    f"{tool} 工具执行",
                    self.orchestrator._tool_explanation(tool),
                    data_flow.get("before_data", {}).get("stage", "previous_artifact"),
                    data_flow.get("after_data", {}).get("stage", "tool_artifact"),
                    {
                        "status": status,
                        "selection_reason": self._selection_reason(tool, tool_plan),
                        "summary": summary,
                        "data_flow": data_flow,
                    },
                )
            )

        rca_context = {
            "opsaug": tool_outputs.get("OpsAug", {}),
            "agent_guidance": guidance,
            "tool_plan": tool_plan,
            "rca_agent_context": context,
            "rca_agent_prompt_guidance": prompt_guidance,
            "rca_graph": {
                "framework": "hermes_skillclaw_rca",
                "process": "hermes_context -> skillclaw_skills -> adaptive_tools -> llm_rca -> failure_learning",
                "agents": [
                    {"id": "hermes_context_engine", "role": "Context and memory manager"},
                    {"id": "skillclaw_skill_runtime", "role": "Skill retrieval and evolution"},
                    {"id": "hermes_tool_router", "role": "Adaptive tool router"},
                    {"id": "hermes_rca_reasoner", "role": "LLM RCA diagnostician"},
                    {"id": "skillclaw_failure_learner", "role": "Failure-driven lifelong learner"},
                ],
            },
        }

        t0 = time.time()
        rca_result = self.orchestrator._run_llm_rca(case_id, detail, rca_context, tool_evidence)
        steps["hermes_llm_rca"] = {
            "duration_s": round(time.time() - t0, 2),
            "status": "ok",
            "explanation": "Hermes RCA Reasoner 读取 SkillClaw 命中的技能、Hermes 上下文记忆和工具证据，输出 Top-K 根因候选。",
            "summary": self.orchestrator._summarize_tool_result(rca_result),
        }
        stages.append(
            self._stage(
                "hermes_rca_reasoner",
                "Hermes RCA Reasoner",
                "使用大模型优先推理；模型不可用或返回不可解析时，显式退回内置因果分析并记录失败原因。",
                previous_artifact.get("stage", "selected_tool_plan") if previous_artifact else "selected_tool_plan",
                "ranked_root_cause_candidates",
                {
                    "model": rca_result.get("model"),
                    "llm_used": rca_result.get("llm_used"),
                    "fallback_used": rca_result.get("fallback_used"),
                    "llm_status": rca_result.get("llm_status"),
                    "candidates": (rca_result.get("parsed_candidates") or [])[:5],
                },
            )
        )

        evaluation = self.orchestrator._evaluate_acc_at_k(
            rca_result,
            detail.get("root_cause_ground_truth", ""),
            detail,
        )
        learning_update = self._build_learning_update(
            evaluation=evaluation,
            tool_plan=tool_plan,
            rca_result=rca_result,
            context=context,
            guidance=guidance,
        )
        stages.append(
            self._stage(
                "skillclaw_failure_learning",
                "SkillClaw 失败学习",
                "把命中/未命中、工具收益、Prompt 规则和上下文压缩策略写成下一轮可复用补丁。",
                "ranked_root_cause_candidates",
                "skill_and_memory_patch",
                learning_update,
            )
        )

        result = {
            "case_id": case_id,
            "case_name": detail.get("case_name", case_id),
            "source": self.data_source.name,
            "source_type": self.data_source.source_type,
            "ground_truth": detail.get("root_cause_ground_truth", ""),
            "status": "completed",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_s": round(time.time() - start, 2),
            "framework": "Hermes + SkillClaw AIOps RCA",
            "repo_status": self.harness.repo_status(),
            "raw_data_summary": raw,
            "hermes_context": context,
            "skillclaw_bundle": skill_bundle,
            "selected_tools": selected_tools,
            "tool_plan": tool_plan,
            "tool_outputs": tool_outputs,
            "tool_evidence": tool_evidence,
            "steps": steps,
            "stages": stages,
            "rca_result": rca_result,
            "evaluation": evaluation,
            "learning_update": learning_update,
        }

        try:
            self.evolution.record_run(result)
        except Exception as exc:
            result["learning_record_error"] = str(exc)
        return result

    def _build_hermes_context(
        self,
        detail: Dict[str, Any],
        raw: Dict[str, Any],
        guidance: Dict[str, Any],
        tool_plan: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        graph = detail.get("service_graph") or {}
        services = graph.get("services") or detail.get("service_inventory") or []
        edges = graph.get("edges") or []
        selected = [item.get("tool") for item in tool_plan if item.get("selected")]
        return {
            "context_contract": {
                "role": "AIOps RCA diagnostician",
                "goal": "定位真实故障注入或企业故障样本的根因服务，并区分根因与传播受害服务。",
                "success_criteria": ["Top1 命中根因服务", "候选解释引用工具/拓扑证据", "记录模型或工具失败原因"],
                "stop_conditions": ["Top-K 候选已输出", "LLM 不可用时完成内置因果兜底", "评估与学习补丁已记录"],
                "budget": {
                    "raw_log_samples": min(3, raw.get("logs", {}).get("count", 0)),
                    "raw_trace_samples": min(3, raw.get("traces", {}).get("count", 0)),
                    "top_metric_values": min(5, raw.get("metrics", {}).get("count", 0)),
                    "selected_tool_count": len(selected),
                },
            },
            "context_layers": {
                "fault_contract": detail.get("fault_injection", {}),
                "modalities": {
                    "logs": {"count": raw.get("logs", {}).get("count", 0), "sample": raw.get("logs", {}).get("sample", [])[:3]},
                    "traces": {"count": raw.get("traces", {}).get("count", 0), "sample": raw.get("traces", {}).get("sample", [])[:3]},
                    "metrics": {"count": raw.get("metrics", {}).get("count", 0), "top_values": raw.get("metrics", {}).get("top_values", [])[:5]},
                    "alerts": {"count": raw.get("alerts", {}).get("count", 0), "sample": raw.get("alerts", {}).get("sample", [])[:3]},
                },
                "topology": {"service_count": len(services), "edge_count": len(edges), "edges": edges[:12]},
                "tool_plan": tool_plan,
            },
            "memory_capsules": {
                "short_term": {
                    "case_id": detail.get("case_id"),
                    "case_name": detail.get("case_name"),
                    "source": detail.get("source"),
                    "selected_tools": selected,
                },
                "semantic": guidance.get("matched_skills", []),
                "failures": guidance.get("failure_lessons", []),
                "prompt_rules": guidance.get("prompt_rules", []),
                "fencing": "Memory is reference context only; ground truth is reserved for evaluator.",
            },
        }

    def _build_prompt_guidance(
        self,
        context: Dict[str, Any],
        guidance: Dict[str, Any],
        tool_plan: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "active_template": {
                "name": "hermes_skillclaw_rca_json_v1",
                "format": '{"candidates":[{"rank":1,"service":"服务名","score":0.95,"reason":"证据"}]}',
                "guardrails": ["只输出 JSON", "服务名必须来自拓扑或服务清单", "不得把高频受害服务直接当根因"],
            },
            "context_contract": context.get("context_contract", {}),
            "memory_capsules": context.get("memory_capsules", {}),
            "base_rules": [
                "先检查服务依赖方向，再解释异常传播。",
                "候选原因必须引用至少一类工具证据或拓扑证据。",
                "如果 LLM 不可用，必须显式记录 fallback_used 与原因。",
            ],
            "learned_patches": guidance.get("prompt_rules", []),
            "failure_contrast_rules": [
                item.get("lesson", str(item))
                for item in guidance.get("failure_lessons", [])
                if item
            ],
            "long_term_memory": guidance.get("matched_skills", []),
            "tool_plan": tool_plan,
        }

    def _harness_state(self, guidance: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "prompt_engine": {
                "learned_patches": guidance.get("prompt_rules", []),
                "failure_contrast_rules": [
                    item.get("lesson", str(item))
                    for item in guidance.get("failure_lessons", [])
                    if item
                ],
            }
        }

    def _run_tool(self, tool: str, case_id: str) -> Dict[str, Any]:
        if tool == "OpsAug":
            return self.orchestrator.opsaug.summarize_case(case_id)
        if tool == "DrainMCP":
            return self.orchestrator.drain_mcp.analyze(case_id)
        if tool == "KPIFailure":
            return self.orchestrator.kpi_failure.analyze(case_id)
        if tool == "DynamicEvolutionarySystem":
            return self.orchestrator.dynamic_evolution.analyze(case_id)
        if tool == "OpsKB":
            return self.orchestrator.opskb.query_knowledge(case_id)
        if tool == "PromCopilot":
            return self.orchestrator.promcopilot.generate_for_case(case_id, "")
        raise ValueError(f"Unknown RCA tool: {tool}")

    def _build_learning_update(
        self,
        *,
        evaluation: Dict[str, Any],
        tool_plan: List[Dict[str, Any]],
        rca_result: Dict[str, Any],
        context: Dict[str, Any],
        guidance: Dict[str, Any],
    ) -> Dict[str, Any]:
        hit = bool(evaluation.get("hit_at_1"))
        selected = [item.get("tool") for item in tool_plan if item.get("selected")]
        skipped = [item.get("tool") for item in tool_plan if not item.get("selected")]
        patch = (
            "保留当前工具路由与上下文压缩策略，下一轮优先复用相同模态证据组合。"
            if hit
            else "增加依赖方向校验和受害服务降权规则；下一轮扩大日志/指标交叉证据，并把本次 Top1 误判写入失败记忆。"
        )
        return {
            "hit_at_1": hit,
            "mrr": evaluation.get("MRR", 0),
            "top_candidate": evaluation.get("top_candidate"),
            "ground_truth_service": evaluation.get("ground_truth_service"),
            "selected_tools": selected,
            "skipped_tools": skipped,
            "prompt_patch": patch,
            "context_patch": {
                "preserve": ["fault_injection_contract", "dependency_direction", "top_metric_values", "tool_data_flow"],
                "compress": ["repeated_logs", "low_signal_normal_metrics"],
            },
            "tool_reward_delta": {
                tool: (0.08 if hit else -0.04)
                for tool in selected
            },
            "memory_write": {
                "type": "success_skill" if hit else "failure_lesson",
                "source_prompt_rules": guidance.get("prompt_rules", [])[-3:],
                "llm_fallback_used": bool(rca_result.get("fallback_used")),
                "context_budget": context.get("context_contract", {}).get("budget", {}),
            },
            "next_iteration": [
                "summarize_session_trajectory",
                "merge_prompt_patch",
                "update_tool_reward_ledger",
                "verify_on_next_fault_case",
            ],
        }

    @staticmethod
    def _selection_reason(tool: str, plan: List[Dict[str, Any]]) -> str:
        for item in plan:
            if item.get("tool") == tool:
                return str(item.get("reason") or "")
        return ""

    @staticmethod
    def _stage(
        stage_id: str,
        title: str,
        analysis: str,
        input_artifact: str,
        output_artifact: str,
        output: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "id": stage_id,
            "run_id": f"hermes-stage-{uuid.uuid4().hex[:8]}",
            "title": title,
            "analysis": analysis,
            "input_artifact": input_artifact,
            "output_artifact": output_artifact,
            "output": output,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
