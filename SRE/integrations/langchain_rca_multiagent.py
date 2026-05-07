# -*- coding: utf-8 -*-
"""LangChain-based RCA agent system.

This module is the RCA runtime. It follows LangChain's core shape: agents, tasks,
graph, sequential process, tools, memory, and callbacks. The external ``langchain``
package is optional; when it is not installed the same blueprint runs through
the local tool/model adapters already present in this project.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_AGENT_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "evolution" / "rca_multiagent_state.json"
VENDORED_LANGCHAIN_REPO = Path(__file__).resolve().parent.parent / "vendor" / "langchain"
VENDORED_LANGCHAIN_PACKAGES = [
    VENDORED_LANGCHAIN_REPO / "libs" / "langchain_v1",
    VENDORED_LANGCHAIN_REPO / "libs" / "core",
    VENDORED_LANGCHAIN_REPO / "libs" / "langchain",
]
VENDORED_AIOPS_EXTENSION = VENDORED_LANGCHAIN_REPO / "aiops_rca" / "rca_graph.py"


class LangChainRCAMultiAgent:
    """LangChain-style RCA agent system with context and memory optimization."""

    TOOL_ORDER = ["OpsAug", "DrainMCP", "KPIFailure", "DynamicEvolutionarySystem", "OpsKB", "PromCopilot"]

    def __init__(self, state_path: str | Path = DEFAULT_AGENT_STATE_PATH) -> None:
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.langchain_repo_path = VENDORED_LANGCHAIN_REPO if VENDORED_LANGCHAIN_REPO.exists() else None
        self.vendored_extension = self._load_vendored_aiops_extension()
        self.langchain_available = self._detect_langchain_package()
        self.langchain_mode = (
            "vendored_langchain_aiops_rca"
            if self.vendored_extension
            else "langchain"
            if self.langchain_available
            else "langchain_compatible_local"
        )

    def get_state(self) -> Dict[str, Any]:
        state = self._load_state()
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        state["langchain_available"] = self.langchain_available
        state["langchain_mode"] = self.langchain_mode
        state["langchain_repo_path"] = str(self.langchain_repo_path) if self.langchain_repo_path else ""
        state["graph_runtime"] = self.runtime_state()
        return state

    def prepare_rca_task(
        self,
        detail: Dict[str, Any],
        guidance: Optional[Dict[str, Any]],
        requested: Optional[List[str]],
        available_tools: List[str],
        raw_data_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        context = self.build_context_package(detail, guidance)
        selected_tools, tool_plan = self.plan_tool_execution(detail, guidance, requested, available_tools)
        prompt_guidance = self.compose_prompt_guidance(guidance, context)
        graph = self.build_graph(detail, context, prompt_guidance, tool_plan, available_tools)
        return {
            "task_id": f"multiagent-task-{uuid.uuid4().hex[:8]}",
            "case_id": detail.get("case_id"),
            "selected_tools": selected_tools,
            "tool_plan": self._with_expected_effects(tool_plan),
            "context_package": context,
            "prompt_guidance": prompt_guidance,
            "graph": graph,
            "current_artifact": {
                "stage": "raw_fault_data",
                "data": raw_data_summary,
                "description": "Multi-Agent RCA 接收的原始 log / trace / metric / alert 样本，尚未经过工具抽取。",
            },
            "execution_trace": [
                {
                    "stage": "graph.sop_contract",
                    "agent": "sop_agent",
                    "summary": "SOP 智能体读取故障注入案例，定义 RCA 目标、成功标准和人工确认门。",
                    "success_criteria": context.get("context_contract", {}).get("success_criteria", []),
                    "process": graph.get("process"),
                },
                {
                    "stage": "graph.context_prompt_contract",
                    "agent": "context_prompt_agent",
                    "summary": "Prompt/上下文管理智能体压缩多模态证据、检索记忆，并生成本轮 Prompt 约束。",
                    "context_budget": context.get("context_contract", {}).get("budget", {}),
                    "prompt_version": prompt_guidance.get("prompt_version"),
                    "memory_capsules": {
                        "semantic": len((context.get("memory_capsules") or {}).get("semantic", []) or []),
                        "failures": len((context.get("memory_capsules") or {}).get("failures", []) or []),
                    },
                },
                {
                    "stage": "graph.tool_plan",
                    "agent": "tool_decision_agent",
                    "summary": "工具调用决策智能体根据数据模态、历史收益和上下文预算选择本轮工具。",
                    "selected_tools": selected_tools,
                }
            ],
        }

    def execute_toolchain(
        self,
        task: Dict[str, Any],
        tool_specs: Dict[str, Dict[str, Any]],
        build_data_flow,
        summarize_result,
    ) -> Dict[str, Any]:
        selected = set(task.get("selected_tools", []))
        graph_tasks = {
            item.get("tool"): item
            for item in (task.get("graph") or {}).get("tasks", [])
            if item.get("tool")
        }
        steps: Dict[str, Dict[str, Any]] = {}
        outputs: Dict[str, Any] = {}
        evidence: List[Dict[str, Any]] = []
        trace = list(task.get("execution_trace", []))
        current_artifact = task.get("current_artifact") or {}

        for name, spec in tool_specs.items():
            step_id = spec.get("step_id", name.lower())
            explanation = spec.get("explanation", "")
            reason = self._selection_reason(name, task.get("tool_plan", []))
            graph_task = graph_tasks.get(name, {})
            if name not in selected:
                flow = build_data_flow(name, {}, "skipped", current_artifact)
                steps[step_id] = {
                    "status": "skipped",
                    "duration_s": 0,
                    "tool_name": name,
                    "graph_agent": graph_task.get("agent", "tool_decision_agent"),
                    "graph_task": graph_task.get("id", f"tool_{name.lower()}"),
                    "explanation": explanation,
                    "selection_reason": reason,
                    "summary": "多 Agent 工具路由器判定当前数据不需要该工具，本轮跳过以控制上下文噪声。",
                    "data_flow": flow,
                    "agent_stage": "tool_routing",
                }
                trace.append({
                    "stage": f"graph.skip_tool:{name}",
                    "agent": "tool_decision_agent",
                    "reason": reason,
                    "artifact_kept": current_artifact.get("stage"),
                })
                continue

            t0 = time.time()
            try:
                payload = spec["runner"]()
                duration = round(time.time() - t0, 2)
                flow = build_data_flow(name, payload, "ok", current_artifact)
                steps[step_id] = {
                    "status": "ok",
                    "duration_s": duration,
                    "tool_name": name,
                    "graph_agent": graph_task.get("agent", "evidence_agent"),
                    "graph_task": graph_task.get("id", f"tool_{name.lower()}"),
                    "explanation": explanation,
                    "selection_reason": reason,
                    "summary": summarize_result(payload),
                    "data_flow": flow,
                    "agent_stage": "tool_execution",
                }
                outputs[name] = payload
                if spec.get("collect_evidence", True) and payload:
                    evidence.append(payload)
                current_artifact = flow.get("after_data") or current_artifact
                trace.append({
                    "stage": f"graph.run_tool:{name}",
                    "agent": "evidence_agent",
                    "duration_s": duration,
                    "before": flow.get("before_data", {}).get("stage"),
                    "after": flow.get("after_data", {}).get("stage"),
                    "change": flow.get("changed_summary"),
                })
            except Exception as e:
                duration = round(time.time() - t0, 2)
                flow = build_data_flow(name, {"error": str(e)}, "error", current_artifact)
                steps[step_id] = {
                    "status": "error",
                    "duration_s": duration,
                    "tool_name": name,
                    "graph_agent": graph_task.get("agent", "evidence_agent"),
                    "graph_task": graph_task.get("id", f"tool_{name.lower()}"),
                    "explanation": explanation,
                    "selection_reason": reason,
                    "error": str(e),
                    "data_flow": flow,
                    "agent_stage": "tool_recovery",
                }
                trace.append({
                    "stage": f"graph.tool_error:{name}",
                    "agent": "evidence_agent",
                    "duration_s": duration,
                    "error": str(e),
                    "recovery": "保留上一阶段 artifact，继续后续可用工具或进入兜底推理。",
                })

        return {
            "steps": steps,
            "outputs": outputs,
            "evidence": evidence,
            "final_artifact": current_artifact,
            "execution_trace": trace,
        }

    def build_context_package(self, detail: Dict[str, Any], guidance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = self._load_state()
        guidance = guidance or {}
        memory_capsules = self._retrieve_memory_capsules(detail, guidance, state)
        context_policy = state["context_engine"]
        context = {
            "context_contract": {
                "role": "LangChain RCA Multi-Agent",
                "goal": "Use tools, model reasoning, topology and memory to localize root causes.",
                "success_criteria": context_policy.get("success_criteria", []),
                "budget": context_policy.get("compression_policy", {}),
                "retrieval_gates": context_policy.get("retrieval_gates", []),
                "learned_context_patches": context_policy.get("learned_context_patches", [])[-8:],
            },
            "context_layers": {
                "task": {
                    "case_id": detail.get("case_id"),
                    "case_name": detail.get("case_name"),
                    "severity": detail.get("severity"),
                    "source": detail.get("source"),
                    "source_type": detail.get("source_type"),
                },
                "modalities": self._summarize_modalities(detail),
                "topology": {
                    "services": detail.get("service_graph", {}).get("services", [])[:30],
                    "edge_count": len(detail.get("service_graph", {}).get("edges", []) or []),
                },
                "memory": memory_capsules,
            },
            "context_filters": context_policy.get("context_filters", []),
            "learned_context_patches": context_policy.get("learned_context_patches", [])[-8:],
            "memory_capsules": memory_capsules,
            "tool_policy": state["tool_engine"].get("trigger_policies", {}),
        }
        if self.vendored_extension and hasattr(self.vendored_extension, "optimize_aiops_context"):
            context["vendored_context_optimization"] = self.vendored_extension.optimize_aiops_context(
                detail, context_policy, memory_capsules
            )
            context["context_contract"]["optimizer"] = "vendored_langchain.aiops_rca"
        return context

    def plan_tool_execution(
        self,
        detail: Dict[str, Any],
        guidance: Optional[Dict[str, Any]],
        requested: Optional[List[str]] = None,
        available_tools: Optional[List[str]] = None,
    ) -> tuple[List[str], List[Dict[str, Any]]]:
        state = self._load_state()
        available_tools = [t for t in (available_tools or self.TOOL_ORDER) if t in self.TOOL_ORDER]
        tool_engine = state["tool_engine"]
        policies = tool_engine.get("trigger_policies", {})
        rewards = tool_engine.get("tool_rewards", {})
        budget = int(tool_engine.get("tool_budget", 5))

        if requested:
            selected = [self._canonical_tool_name(t) for t in requested]
            selected = [t for t in selected if t in available_tools]
            requested_plan = [
                {
                    "tool": tool,
                    "selected": True,
                    "reason": "人工指定工具，Multi-Agent 保留人工优先级。",
                    "expected_effect": self._expected_tool_effect(tool),
                    "agent": "tool_decision_agent",
                    "trigger_policy": policies.get(tool, ""),
                    "learned_reward": rewards.get(tool, {}).get("score", 0.5),
                }
                for tool in selected
            ]
            return selected, self._with_expected_effects(requested_plan)

        availability = self._data_availability(detail)
        candidates = {
            "OpsAug": (availability["modality_count"] >= 2, "多模态证据足够，先做跨模态摘要。", "多模态不足，跳过融合工具。"),
            "DrainMCP": (availability["has_logs"], "存在日志，提取日志模板和异常模式。", "没有日志条目，跳过日志工具。"),
            "KPIFailure": (availability["has_metrics"], "存在指标，提取 KPI 异常和服务得分。", "没有指标序列，跳过指标工具。"),
            "DynamicEvolutionarySystem": (
                availability["has_graph"] and (availability["edge_count"] >= 6 or detail.get("source_type") == "dynamic"),
                "拓扑具备传播分析价值，检查系统级动态影响。",
                "拓扑规模不足，动态演化收益有限。",
            ),
            "OpsKB": (availability["has_graph"] and availability["service_count"] >= 3, "服务依赖足够，检索架构知识以约束模型。", "服务清单不足，跳过知识检索。"),
            "PromCopilot": (availability["has_metric_columns"], "存在指标列，生成 PromQL 验证查询。", "没有可查询指标列，跳过 PromQL。"),
        }

        external_weights = (guidance or {}).get("tool_weights", {})
        planned = []
        for tool in available_tools:
            ok, yes, no = candidates.get(tool, (False, "", "未定义触发条件。"))
            reward = rewards.get(tool, {})
            learned_score = float(reward.get("score", 0.5))
            external = external_weights.get(tool.lower()) or external_weights.get(tool)
            if isinstance(external, dict):
                learned_score = round((learned_score + float(external.get("score", learned_score))) / 2, 4)
            reason = yes if ok else no
            if ok and learned_score < 0.18 and tool not in {"OpsAug", "DrainMCP", "KPIFailure"}:
                ok = False
                reason = f"历史奖励较低（{learned_score:.2f}），本轮不放入上下文。"
            planned.append({
                "tool": tool,
                "selected": bool(ok),
                "reason": reason,
                "agent": "tool_decision_agent",
                "trigger_policy": policies.get(tool, ""),
                "learned_reward": learned_score,
                "historical_runs": reward.get("runs", 0),
            })

        selected_plan = [p for p in planned if p["selected"]]
        selected_plan.sort(key=lambda p: p.get("learned_reward", 0.5), reverse=True)
        selected = [p["tool"] for p in selected_plan[:budget]]
        selected_set = set(selected)
        for item in planned:
            if item["selected"] and item["tool"] not in selected_set:
                item["selected"] = False
                item["reason"] = f"Multi-Agent 工具预算为 {budget}，该工具本轮延后。"
        if not selected:
            selected = ["OpsAug"]
            planned = [{
                "tool": "OpsAug",
                "selected": True,
                "reason": "证据较少，启用最小上下文摘要作为兜底。",
                "agent": "tool_decision_agent",
                "trigger_policy": policies.get("OpsAug", ""),
                "learned_reward": rewards.get("OpsAug", {}).get("score", 0.5),
                "expected_effect": self._expected_tool_effect("OpsAug"),
            }]
        return selected, self._with_expected_effects(planned)

    def compose_prompt_guidance(
        self,
        guidance: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        state = self._load_state()
        prompt = state["prompt_engine"]
        memory = state["memory_engine"]
        context = context or {}
        guidance_pack = {
            "prompt_version": prompt.get("version", 1),
            "active_template": prompt.get("active_template", {}),
            "base_rules": prompt.get("base_rules", []),
            "learned_patches": prompt.get("learned_patches", [])[-8:],
            "failure_contrast_rules": prompt.get("failure_contrast_rules", [])[-6:],
            "memory_capsules": context.get("memory_capsules", {}),
            "learned_context_patches": context.get("learned_context_patches", []),
            "long_term_memory": (memory.get("semantic_memory", []) + memory.get("long_term_memory", []))[-6:],
            "context_contract": context.get("context_contract", {}),
            "external_guidance": guidance or {},
        }
        if self.vendored_extension and hasattr(self.vendored_extension, "build_prompt_pack"):
            guidance_pack["vendored_prompt_pack"] = self.vendored_extension.build_prompt_pack(
                prompt, context, context.get("memory_capsules", {})
            )
        return guidance_pack

    def build_graph(
        self,
        detail: Dict[str, Any],
        context: Dict[str, Any],
        prompt_guidance: Dict[str, Any],
        tool_plan: List[Dict[str, Any]],
        tool_order: List[str],
    ) -> Dict[str, Any]:
        selected_tools = [item.get("tool") for item in tool_plan if item.get("selected")]
        selected_tools = [tool for tool in selected_tools if tool]
        if self.vendored_extension and hasattr(self.vendored_extension, "build_aiops_rca_graph"):
            return self.vendored_extension.build_aiops_rca_graph(
                case_id=detail.get("case_id"),
                selected_tools=selected_tools,
                tool_plan=tool_plan,
                context=context,
                prompt_guidance=prompt_guidance,
                tool_order=tool_order,
                langchain_repo_path=str(self.langchain_repo_path or ""),
            )
        return {
            "graph_id": f"rca-graph-{uuid.uuid4().hex[:8]}",
            "framework": self.langchain_mode,
            "langchain_available": self.langchain_available,
            "process": "sequential",
            "case_id": detail.get("case_id"),
            "agents": self.agent_blueprint(selected_tools),
            "tasks": self.task_blueprint(tool_plan),
            "memory": {
                "enabled": True,
                "short_term": (context.get("memory_capsules") or {}).get("short_term", {}),
                "semantic": (context.get("memory_capsules") or {}).get("semantic", []),
                "failure": (context.get("memory_capsules") or {}).get("failures", []),
                "prompt_version": prompt_guidance.get("prompt_version"),
            },
            "tools": {
                "available": tool_order,
                "selected": selected_tools,
                "routing_policy": context.get("tool_policy", {}),
            },
            "callbacks": ["record_artifact_diff", "record_tool_reward", "record_prompt_patch", "record_failure_trajectory"],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def update_from_rca(self, result: Dict[str, Any]) -> Dict[str, Any]:
        state = self._load_state()
        ev = result.get("evaluation", {}) or {}
        rca = result.get("rca_result", {}) or {}
        hit = bool(ev.get("hit_at_1"))
        top = ev.get("top_candidate") or rca.get("primary_root_cause") or ""
        gt = ev.get("ground_truth_service") or ""
        tools_used = [
            step.get("tool_name")
            for step in (result.get("steps") or {}).values()
            if isinstance(step, dict) and step.get("tool_name") and step.get("status") == "ok"
        ]
        event = {
            "id": f"rca-multiagent-run-{uuid.uuid4().hex[:8]}",
            "case_id": result.get("case_id"),
            "source": result.get("source"),
            "hit_at_1": hit,
            "mrr": ev.get("MRR", 0),
            "top_candidate": top,
            "ground_truth_service": gt,
            "tools_used": tools_used,
            "llm_used": bool(rca.get("llm_used")),
            "fallback_used": bool(rca.get("fallback_used")),
            "graph_id": (result.get("rca_graph") or {}).get("graph_id"),
            "prompt_version": (result.get("rca_agent_prompt_guidance") or {}).get("prompt_version"),
            "timestamp": time.time(),
        }
        state["learning_events"].append(event)
        state["learning_events"] = state["learning_events"][-160:]
        state["agent_state"]["iterations"] += 1
        state["agent_state"]["policy_version"] = int(state["agent_state"].get("policy_version", 1)) + 1
        state["agent_state"]["last_run_id"] = event["id"]
        state["agent_state"]["last_update_reason"] = "success_memory_prompt_update" if hit else "failure_trajectory_patch"

        memory = state["memory_engine"]
        memory["short_term_memory"]["last_case"] = {
            "case_id": result.get("case_id"),
            "top_candidate": top,
            "ground_truth": gt,
            "tools_used": tools_used,
        }
        memory.setdefault("episodic_memory", []).append(event)
        memory["episodic_memory"] = memory["episodic_memory"][-50:]
        self._update_tool_rewards(state, result.get("tool_plan", []), hit)
        if hit:
            self._update_success_policy(state, result, event)
        else:
            self._update_failure_policy(state, result, event)

        lifelong = state["lifelong_learning"]
        lifelong["metrics"] = self._recompute_metrics(state["learning_events"])
        lifelong.setdefault("execution_logs", []).append({
            "message": "LangChain Multi-Agent learned from outcome",
            "case_id": result.get("case_id"),
            "hit_at_1": hit,
            "timestamp": event["timestamp"],
        })
        lifelong["execution_logs"] = lifelong["execution_logs"][-80:]
        self._save_state(state)
        return event

    def register_enterprise_tool(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        state = self._load_state()
        tool = {
            "id": spec.get("id") or f"enterprise-tool-{uuid.uuid4().hex[:8]}",
            "name": spec.get("name") or "Enterprise Tool",
            "description": spec.get("description", ""),
            "input_modalities": spec.get("input_modalities", []),
            "output_contract": spec.get("output_contract", "summary + evidence pointers"),
            "trigger_condition": spec.get("trigger_condition", "manual or policy-driven"),
            "endpoint": spec.get("endpoint", ""),
            "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "registered",
        }
        tools = state["tool_engine"].setdefault("enterprise_tools", [])
        existing = next((idx for idx, item in enumerate(tools) if item.get("id") == tool["id"]), None)
        if existing is None:
            tools.append(tool)
        else:
            tools[existing] = tool
        state["agent_state"]["policy_version"] += 1
        state["agent_state"]["last_update_reason"] = "enterprise_tool_registered"
        self._save_state(state)
        return {"status": "ok", "tool": tool}

    def runtime_state(self) -> Dict[str, Any]:
        return {
            "label": "LangChain RCA Multi-Agent",
            "framework": self.langchain_mode,
            "langchain_available": self.langchain_available,
            "langchain_repo_path": str(self.langchain_repo_path) if self.langchain_repo_path else "",
            "process": "fault_injection_to_multiagent_rca",
            "memory_enabled": True,
            "checkpoint": "after_each_task",
            "agents": self.agent_blueprint([]),
            "task_flow": [
                "sop_contract",
                "context_prompt_contract",
                "memory_retrieval",
                "route_tools",
                "tool_execution",
                "model_rca",
                "evaluate_and_learn",
            ],
            "callbacks": ["record_artifact_diff", "record_tool_reward", "record_prompt_patch", "record_failure_trajectory"],
        }

    def build_tool_plan_board(
        self,
        detail: Dict[str, Any],
        guidance: Optional[Dict[str, Any]],
        requested: Optional[List[str]] = None,
        available_tools: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        state = self._load_state()
        context = self.build_context_package(detail, guidance)
        selected, plan = self.plan_tool_execution(detail, guidance, requested, available_tools or self.TOOL_ORDER)
        ordered = [item for item in plan if item.get("selected")]
        skipped = [item for item in plan if not item.get("selected")]
        catalog = self._build_available_tool_catalog(plan, state)
        workflow = self.build_agent_workflow(detail, context, plan, catalog, selected)
        return {
            "status": "ok",
            "planner": "langchain_multiagent_tool_decision_agent",
            "framework": self.langchain_mode,
            "langchain_repo_path": str(self.langchain_repo_path) if self.langchain_repo_path else "",
            "case_id": detail.get("case_id"),
            "case_name": detail.get("case_name"),
            "available_tool_count": len(catalog),
            "built_in_tool_count": len([item for item in catalog if item.get("kind") == "built_in"]),
            "enterprise_tool_count": len([item for item in catalog if item.get("kind") == "enterprise"]),
            "available_tool_catalog": catalog,
            "selected_tools": selected,
            "ordered_plan": [
                {
                    "order": idx + 1,
                    "tool": item.get("tool"),
                    "agent": item.get("agent", "tool_decision_agent"),
                    "reason": item.get("reason", ""),
                    "expected_effect": item.get("expected_effect") or self._expected_tool_effect(item.get("tool")),
                    "learned_reward": item.get("learned_reward", 0.5),
                    "trigger_policy": item.get("trigger_policy", ""),
                }
                for idx, item in enumerate(ordered)
            ],
            "skipped_tools": [
                {
                    "tool": item.get("tool"),
                    "reason": item.get("reason", ""),
                    "expected_effect": "本轮跳过，避免把低收益信息塞进模型上下文。",
                    "learned_reward": item.get("learned_reward", 0.5),
                }
                for item in skipped
            ],
            "context_contract": context.get("context_contract", {}),
            "sop_contract": workflow.get("sop_contract", {}),
            "agent_workflow": workflow,
            "explanation": "工具路由 Agent 根据数据模态、拓扑规模、历史收益和上下文预算决定是否调用工具。",
        }

    def build_agent_workflow(
        self,
        detail: Dict[str, Any],
        context: Dict[str, Any],
        plan: List[Dict[str, Any]],
        catalog: List[Dict[str, Any]],
        selected: List[str],
    ) -> Dict[str, Any]:
        """Build a visible LangChain-style RCA multi-agent workflow for the UI."""
        state = self._load_state()
        selected_set = set(selected or [])
        selected_tools = [item for item in plan if item.get("selected")]
        skipped_tools = [item for item in plan if not item.get("selected")]
        availability = self._data_availability(detail)
        modalities = self._summarize_modalities(detail)
        memory = context.get("memory_capsules") or {}
        prompt_state = state.get("prompt_engine", {})
        context_contract = context.get("context_contract", {})
        case_label = detail.get("case_name") or detail.get("case_id") or "fault case"
        graph = detail.get("service_graph") or {}
        services = graph.get("services") or detail.get("service_inventory") or []
        edges = graph.get("edges") or []
        enterprise_tools = [item for item in catalog if item.get("kind") == "enterprise"]
        tool_route = " -> ".join(selected or []) if selected else "OpsAug"
        repo_path = str(self.langchain_repo_path) if self.langchain_repo_path else ""

        sop_contract = {
            "case": case_label,
            "role": "AIOps RCA commander",
            "objective": "把故障注入后的多模态数据转换为可验证的 Top-K 根因定位结果。",
            "success_criteria": context_contract.get("success_criteria", []),
            "human_gates": ["确认工具预案", "确认执行多智能体诊断", "确认采纳 RCA 结果并写入学习记忆", "诊断完成后确认是否恢复故障"],
            "stop_conditions": ["关键数据缺失且无法兜底", "人工终止", "工具或模型连续失败后进入内置因果分析并标记 fallback"],
        }

        agents = [
            {
                "id": "sop_agent",
                "name": "SOP 智能体",
                "role": "Runbook / SOP Orchestrator",
                "objective": "把故障注入案例拆成 RCA 任务、成功标准、停止条件和人工确认门。",
                "inputs": ["case metadata", "fault injection point", "service graph"],
                "outputs": ["sop_contract", "task_acceptance_criteria"],
                "memory": ["runbook memory", "failure playbooks"],
                "tools": [],
                "status": "planned",
            },
            {
                "id": "context_prompt_agent",
                "name": "Prompt/上下文管理智能体",
                "role": "Context and Prompt Manager",
                "objective": "按层组织 log/trace/metric/alert/topology，只把摘要、关键证据和记忆补丁交给后续 Agent。",
                "inputs": ["raw multimodal evidence", "context budget", "retrieved memory"],
                "outputs": ["context_contract", "prompt_pack", "artifact_budget"],
                "memory": ["short_term", "semantic", "failure"],
                "tools": [],
                "status": "planned",
            },
            {
                "id": "memory_agent",
                "name": "记忆检索智能体",
                "role": "Memory Retriever",
                "objective": "检索相似成功策略和失败反例，给诊断模型提供反事实约束。",
                "inputs": ["case keys", "source", "fault category", "service list"],
                "outputs": ["semantic_memory", "failure_memory", "prompt_patches"],
                "memory": ["semantic", "episodic", "negative trajectories"],
                "tools": [],
                "status": "planned",
            },
            {
                "id": "tool_decision_agent",
                "name": "工具调用决策智能体",
                "role": "Adaptive Tool Router",
                "objective": "不是全量调用工具，而是按数据可用性、拓扑规模、历史 reward 和上下文预算选择工具。",
                "inputs": ["modality availability", "tool rewards", "enterprise tool registry"],
                "outputs": ["ordered_tool_plan", "skipped_tool_reasons"],
                "memory": ["tool_rewards", "negative trajectories"],
                "tools": [item.get("tool") for item in catalog],
                "status": "active" if selected_tools else "planned",
            },
            {
                "id": "evidence_agent",
                "name": "多模态证据分析智能体",
                "role": "Log / Trace / Metric Evidence Analyst",
                "objective": "执行被选中的工具，并产出上一阶段数据与本阶段 artifact diff。",
                "inputs": ["raw data", "selected tool plan", "previous artifact"],
                "outputs": ["tool summaries", "top evidence", "artifact diffs"],
                "memory": ["artifact_history"],
                "tools": selected,
                "status": "planned",
            },
            {
                "id": "diagnosis_agent",
                "name": "诊断智能体",
                "role": "LLM RCA Reasoner",
                "objective": "把工具证据、拓扑传播、Prompt 补丁和记忆约束交给大模型生成 Top-K RCA。",
                "inputs": ["tool evidence", "system propagation", "prompt pack", "memory capsules"],
                "outputs": ["ranked_root_cause_candidates", "evidence_citations", "uncertainty"],
                "memory": ["semantic", "failure"],
                "tools": [],
                "status": "planned",
            },
            {
                "id": "critic_learning_agent",
                "name": "评估/终身学习智能体",
                "role": "Independent Evaluator and Lifelong Learner",
                "objective": "用 ACC@K/MRR、LLM 使用状态和失败轨迹更新记忆、Prompt、上下文过滤和工具 reward。",
                "inputs": ["RCA candidates", "ground truth", "execution trace"],
                "outputs": ["memory update", "prompt patch", "tool reward update"],
                "memory": ["learning_events", "curriculum"],
                "tools": [],
                "status": "planned",
            },
            {
                "id": "enterprise_gateway_agent",
                "name": "企业工具网关智能体",
                "role": "Enterprise Tool Adapter",
                "objective": "保留企业内部工具接入口，注册后由工具决策智能体重新评估是否进入调用链。",
                "inputs": ["tool spec", "endpoint", "input modalities", "output contract"],
                "outputs": ["enterprise tool registry", "adapter contract"],
                "memory": ["tool registry"],
                "tools": [item.get("tool") for item in enterprise_tools],
                "status": "ready" if enterprise_tools else "available",
            },
        ]

        stages = [
            {
                "order": 1,
                "agent": "sop_agent",
                "title": "故障注入接入与 SOP 定轨",
                "action": f"接收 {case_label}，确认根因定位目标、成功标准和人工确认点。",
                "input_artifact": "fault_injection_case",
                "output_artifact": "sop_contract",
                "handoff_to": "context_prompt_agent",
                "human_gate": False,
            },
            {
                "order": 2,
                "agent": "context_prompt_agent",
                "title": "结构化上下文与 Prompt 优化",
                "action": "按 log / trace / metric / alert / topology 分层压缩，注入 Prompt 规则和历史补丁。",
                "input_artifact": "raw_multimodal_fault_data",
                "output_artifact": "context_contract + prompt_pack",
                "handoff_to": "memory_agent",
                "human_gate": False,
            },
            {
                "order": 3,
                "agent": "memory_agent",
                "title": "相似案例记忆检索",
                "action": "读取短期、语义和失败记忆，形成反事实 guardrail，防止把受害服务误判为根因。",
                "input_artifact": "context_contract",
                "output_artifact": "memory_capsules",
                "handoff_to": "tool_decision_agent",
                "human_gate": False,
            },
            {
                "order": 4,
                "agent": "tool_decision_agent",
                "title": "工具调用预案",
                "action": f"从 {len(catalog)} 个工具中选择 {len(selected_tools)} 个：{tool_route}。",
                "input_artifact": "context + memory + tool reward",
                "output_artifact": "ordered_tool_plan",
                "handoff_to": "evidence_agent",
                "human_gate": True,
            },
            {
                "order": 5,
                "agent": "evidence_agent",
                "title": "选择性工具执行",
                "action": "只执行被选中的工具，每一步输出处理前后数据对比和证据摘要。",
                "input_artifact": "ordered_tool_plan + previous artifact",
                "output_artifact": "tool_evidence_artifacts",
                "handoff_to": "diagnosis_agent",
                "human_gate": True,
            },
            {
                "order": 6,
                "agent": "diagnosis_agent",
                "title": "LLM + 工具证据根因推理",
                "action": "使用大模型、工具证据、拓扑传播和记忆约束生成 Top-K 根因候选。",
                "input_artifact": "tool_evidence + prompt_pack + propagation context",
                "output_artifact": "ranked_rca_candidates",
                "handoff_to": "critic_learning_agent",
                "human_gate": True,
            },
            {
                "order": 7,
                "agent": "critic_learning_agent",
                "title": "评估与终身学习",
                "action": "按 ACC@K/MRR 评估结果，成功写入策略记忆，失败写入负轨迹并更新 Prompt/上下文/工具 reward。",
                "input_artifact": "ranked_rca_candidates + ground_truth",
                "output_artifact": "memory_prompt_tool_updates",
                "handoff_to": "END",
                "human_gate": False,
            },
        ]

        handoffs = [
            {
                "from": "sop_agent",
                "to": "context_prompt_agent",
                "contract": "角色、目标、成功标准、停止条件、人工确认门。",
            },
            {
                "from": "context_prompt_agent",
                "to": "memory_agent",
                "contract": "分层证据摘要、上下文预算、Prompt 版本和检索关键词。",
            },
            {
                "from": "memory_agent",
                "to": "tool_decision_agent",
                "contract": "相似成功策略、失败反例、不得误判的受害服务约束。",
            },
            {
                "from": "tool_decision_agent",
                "to": "evidence_agent",
                "contract": "被选工具顺序、未选工具原因、预期产物和工具输出结构。",
            },
            {
                "from": "evidence_agent",
                "to": "diagnosis_agent",
                "contract": "每个工具的处理前后数据、证据摘要、异常服务和 artifact diff。",
            },
            {
                "from": "diagnosis_agent",
                "to": "critic_learning_agent",
                "contract": "Top-K 候选、证据引用、模型调用状态和不确定性。",
            },
        ]

        return {
            "flow_id": f"fault-to-rca-flow-{uuid.uuid4().hex[:8]}",
            "process": "fault_injection_to_multiagent_rca",
            "framework": self.langchain_mode,
            "langchain_repo_path": repo_path,
            "langchain_source": "git@github.com:langchain-ai/langchain.git",
            "case_id": detail.get("case_id"),
            "case_name": case_label,
            "sop_contract": sop_contract,
            "agents": agents,
            "stages": stages,
            "handoffs": handoffs,
            "human_gates": sop_contract["human_gates"],
            "data_readiness": {
                "modalities": modalities,
                "availability": availability,
                "service_count": len(services),
                "edge_count": len(edges),
            },
            "tool_decision": {
                "available_tool_count": len(catalog),
                "selected_tools": selected,
                "skipped_tools": [item.get("tool") for item in skipped_tools],
                "enterprise_tools": [item.get("tool") for item in enterprise_tools],
            },
            "prompt_context": {
                "prompt_version": prompt_state.get("version", 1),
                "active_template": prompt_state.get("active_template", {}),
                "learned_patches": prompt_state.get("learned_patches", [])[-5:],
                "failure_contrast_rules": prompt_state.get("failure_contrast_rules", [])[-5:],
                "context_budget": context_contract.get("budget", {}),
                "memory_capsules": {
                    "semantic": len(memory.get("semantic") or []),
                    "failures": len(memory.get("failures") or []),
                    "short_term": len(memory.get("short_term") or {}),
                },
            },
        }

    def _build_available_tool_catalog(self, plan: List[Dict[str, Any]], state: Dict[str, Any]) -> List[Dict[str, Any]]:
        by_tool = {item.get("tool"): item for item in plan if item.get("tool")}
        catalog: List[Dict[str, Any]] = []
        for tool in self.TOOL_ORDER:
            item = by_tool.get(tool, {})
            catalog.append({
                "tool": tool,
                "kind": "built_in",
                "selected": bool(item.get("selected")),
                "executable": True,
                "reason": item.get("reason", "等待工具路由 Agent 评估。"),
                "expected_effect": item.get("expected_effect") or self._expected_tool_effect(tool),
                "learned_reward": item.get("learned_reward", state.get("tool_engine", {}).get("tool_rewards", {}).get(tool, {}).get("score", 0.5)),
                "trigger_policy": item.get("trigger_policy", state.get("tool_engine", {}).get("trigger_policies", {}).get(tool, "")),
            })
        for tool in state.get("tool_engine", {}).get("enterprise_tools", []) or []:
            name = str(tool.get("name") or tool.get("id") or "Enterprise Tool")
            catalog.append({
                "tool": name,
                "kind": "enterprise",
                "selected": False,
                "executable": False,
                "reason": "已接入工具池；当前作为上下文候选工具，执行适配器接好后可进入自动调用链。",
                "expected_effect": tool.get("output_contract") or "企业内部工具返回结构化证据摘要。",
                "learned_reward": 0.5,
                "trigger_policy": tool.get("trigger_condition") or "manual or policy-driven",
                "endpoint": tool.get("endpoint", ""),
                "status": tool.get("status", "registered"),
            })
        return catalog

    def agent_blueprint(self, selected_tools: List[str]) -> List[Dict[str, Any]]:
        return [
            {"id": "sop_agent", "role": "SOP / Runbook Orchestrator", "goal": "Define RCA objective, success criteria, stop conditions and human confirmation gates.", "memory": ["runbook_memory", "failure_playbooks"], "tools": []},
            {"id": "context_prompt_agent", "role": "Prompt and Context Manager", "goal": "Compress raw evidence into context contract, prompt pack and artifact budget.", "memory": ["short_term", "semantic", "failure"], "tools": []},
            {"id": "memory_agent", "role": "Memory Retriever", "goal": "Retrieve similar successful strategies and failure counterexamples.", "memory": ["semantic", "episodic", "negative_trajectories"], "tools": []},
            {"id": "tool_decision_agent", "role": "Adaptive Tool Decision Agent", "goal": "Choose tools by data availability, learned rewards and context budget instead of running everything.", "memory": ["tool_rewards", "negative_trajectories"], "tools": selected_tools},
            {"id": "evidence_agent", "role": "Multimodal Evidence Analyst", "goal": "Run selected tools and hand off before/after artifact diffs.", "memory": ["artifact_history"], "tools": selected_tools},
            {"id": "diagnosis_agent", "role": "LLM RCA Diagnostician", "goal": "Use model, tools, topology and memory to output Top-K RCA candidates.", "memory": ["semantic", "failure"], "tools": []},
            {"id": "critic_learning_agent", "role": "Evaluator and Lifelong Learning Critic", "goal": "Turn RCA outcomes into memory, prompt, context and tool-routing updates.", "memory": ["learning_events", "curriculum"], "tools": []},
            {"id": "enterprise_gateway_agent", "role": "Enterprise Tool Gateway", "goal": "Expose integration hooks for internal enterprise tools and adapter contracts.", "memory": ["tool_registry"], "tools": []},
        ]

    def task_blueprint(self, tool_plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        tasks = [
            {"id": "sop_contract", "agent": "sop_agent", "description": "Define case objective, success criteria, stop conditions and human gates.", "expected_output": "sop_contract", "guardrail": "Never start RCA without a case and confirmation boundary.", "status": "planned"},
            {"id": "context_prompt_contract", "agent": "context_prompt_agent", "description": "Build context contract, prompt pack, retrieval gates and input budget.", "expected_output": "context_package + prompt_pack", "guardrail": "Do not pass unfiltered raw evidence to the LLM.", "status": "planned"},
            {"id": "memory_retrieval", "agent": "memory_agent", "description": "Retrieve semantic memory and failure counterexamples for this source/service/fault type.", "expected_output": "memory_capsules", "guardrail": "Use memory as constraints, not as ground truth.", "status": "planned"},
            {"id": "route_tools", "agent": "tool_decision_agent", "description": "Select tools by modality, reward and budget.", "expected_output": "tool_plan", "guardrail": "Every selected or skipped tool must have a visible reason.", "status": "planned"},
        ]
        for item in tool_plan:
            tool = item.get("tool", "")
            tasks.append({
                "id": f"tool_{tool.lower()}",
                "agent": "evidence_agent",
                "tool": tool,
                "description": item.get("reason", ""),
                "expected_output": self.tool_output_contract(tool),
                "guardrail": "Return summary, top evidence and artifact diff only.",
                "status": "selected" if item.get("selected") else "skipped",
            })
        tasks.extend([
            {"id": "model_rca", "agent": "diagnosis_agent", "description": "Reason over selected tool evidence, topology, prompt patches and memory.", "expected_output": "ranked_root_cause_candidates", "guardrail": "Cite evidence and compare root cause vs victim symptom.", "status": "planned"},
            {"id": "evaluate_and_learn", "agent": "critic_learning_agent", "description": "Evaluate ACC@K/MRR and update memory, prompts, context patches and tool rewards.", "expected_output": "memory_prompt_tool_updates", "guardrail": "Do not mark fallback as LLM result.", "status": "planned"},
        ])
        return tasks

    def _load_state(self) -> Dict[str, Any]:
        if self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("agent_name"):
                    return self._ensure_state(data)
            except (OSError, json.JSONDecodeError):
                pass
        state = self._default_state()
        self._save_state(state)
        return state

    def _detect_langchain_package(self) -> bool:
        for package_dir in reversed(VENDORED_LANGCHAIN_PACKAGES):
            if package_dir.exists():
                package_path = str(package_dir)
                if package_path not in sys.path:
                    sys.path.insert(0, package_path)
        return importlib.util.find_spec("langchain") is not None

    def _load_vendored_aiops_extension(self):
        if not VENDORED_AIOPS_EXTENSION.exists():
            return None
        spec = importlib.util.spec_from_file_location("ops_factory_vendored_langchain_aiops_rca", VENDORED_AIOPS_EXTENSION)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            return module
        except Exception:
            return None

    def _ensure_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        default = self._default_state()
        for key, value in default.items():
            state.setdefault(key, value)
            if isinstance(value, dict):
                for sub_key, sub_val in value.items():
                    state[key].setdefault(sub_key, sub_val)
        for tool, reward in default["tool_engine"]["tool_rewards"].items():
            state["tool_engine"].setdefault("tool_rewards", {}).setdefault(tool, reward)
        return state

    def _save_state(self, state: Dict[str, Any]) -> None:
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _default_state(self) -> Dict[str, Any]:
        rewards = {tool: {"score": 0.5, "runs": 0, "successes": 0, "failures": 0} for tool in self.TOOL_ORDER}
        return {
            "agent_name": "Ops Factory LangChain Multi-Agent RCA",
            "description": "基于本地 clone 的 LangChain runnable/graph/tool/memory 思路构建 RCA 多 Agent 模块，负责上下文、记忆、工具路由、大模型和终身学习。",
            "agent_state": {"iterations": 0, "policy_version": 1, "last_update_reason": "initial", "last_run_id": ""},
            "context_engine": {
                "success_criteria": ["候选根因必须引用工具证据或拓扑证据", "区分根因服务与受害服务", "保留 Top-K 不确定性并计算 ACC@K/MRR"],
                "context_filters": ["case metadata", "logs", "traces", "metrics", "alerts", "topology", "retrieved memory"],
                "retrieval_gates": ["只检索与当前平台/服务/故障类型匹配的记忆", "直接证据优先于高频症状", "进入模型前只给摘要、Top evidence 和 artifact diff"],
                "compression_policy": {"raw_budget": "samples only", "tool_result_budget": "summary + top evidence + data diff", "drop_rules": ["drop repetitive logs", "drop low-signal metrics", "keep critical trace spans"]},
                "learned_context_patches": [],
            },
            "memory_engine": {
                "short_term_memory": {},
                "episodic_memory": [],
                "semantic_memory": [],
                "failure_memory": [],
                "long_term_memory": [],
                "retrieval_policy": {"semantic_top_k": 5, "failure_top_k": 5, "match_keys": ["source", "case_name", "fault_category", "service"]},
            },
            "prompt_engine": {
                "version": 1,
                "active_template": {"name": "langchain_rca_contrastive_json_v1", "guards": ["cite tool evidence", "compare root cause vs victim", "output JSON only"]},
                "base_rules": ["先构建故障传播链，再给 Top-K 根因候选。", "区分直接根因证据、传播症状和受害服务。", "每个候选必须引用至少一种工具证据和一种上下文证据。"],
                "learned_patches": [],
                "failure_contrast_rules": [],
            },
            "tool_engine": {
                "trigger_policies": {
                    "OpsAug": "when at least two evidence modalities exist",
                    "DrainMCP": "only when logs.entries exist",
                    "KPIFailure": "only when metrics.series_summary or raw_series exist",
                    "PromCopilot": "only when metric_columns exist",
                    "OpsKB": "only when service topology has enough services",
                    "DynamicEvolutionarySystem": "only when topology suggests system-level propagation",
                },
                "tool_budget": 5,
                "result_contract": "extract, filter, summarize, and return artifact diff before passing to the model",
                "tool_rewards": rewards,
                "enterprise_tools": [],
            },
            "lifelong_learning": {
                "algorithm": "trajectory learning from successes and failures",
                "metrics": {"runs": 0, "top1_success_rate": 0, "avg_mrr": 0, "llm_usage_rate": 0},
                "success_patterns": [],
                "negative_trajectories": [],
                "update_rules": [
                    "每轮 RCA 后记录 ACC@K/MRR、Top1 命中和工具链。",
                    "成功 case 写入语义记忆、Prompt 补丁和可复用上下文补丁。",
                    "失败 case 写入负轨迹、反事实对比规则，并降低低收益工具优先级。",
                    "下一轮 RCA 先检索相关记忆，再按数据可用性和历史收益选择工具。",
                ],
                "curriculum": [],
                "execution_logs": [],
            },
            "learning_events": [],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _retrieve_memory_capsules(self, detail: Dict[str, Any], guidance: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        memory = state["memory_engine"]
        terms = " ".join([
            str(detail.get("source", "")),
            str(detail.get("source_type", "")),
            str(detail.get("case_name", "")),
            str(detail.get("fault_category", "")),
            " ".join(detail.get("service_graph", {}).get("services", [])[:8]),
        ]).lower()

        def rank(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            scored = []
            tokens = [t for t in terms.split() if t]
            for item in items:
                text = json.dumps(item, ensure_ascii=False, default=str).lower()
                scored.append((sum(1 for token in tokens if token in text), item))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [item for _, item in scored[:5]]

        external_failures = guidance.get("failure_lessons", []) if isinstance(guidance, dict) else []
        return {
            "short_term": memory.get("short_term_memory", {}),
            "semantic": rank(memory.get("semantic_memory", []) + memory.get("long_term_memory", [])),
            "failures": rank(memory.get("failure_memory", []) + external_failures),
            "retrieval_policy": memory.get("retrieval_policy", {}),
        }

    def _update_tool_rewards(self, state: Dict[str, Any], tool_plan: List[Dict[str, Any]], hit: bool) -> None:
        rewards = state["tool_engine"].setdefault("tool_rewards", {})
        for item in tool_plan:
            tool = item.get("tool")
            if not tool or not item.get("selected"):
                continue
            stat = rewards.setdefault(tool, {"score": 0.5, "runs": 0, "successes": 0, "failures": 0})
            previous = float(stat.get("score", 0.5))
            outcome = 1.0 if hit else 0.12
            stat["score"] = round(previous * 0.78 + outcome * 0.22, 4)
            stat["runs"] = int(stat.get("runs", 0)) + 1
            stat["successes" if hit else "failures"] = int(stat.get("successes" if hit else "failures", 0)) + 1
            stat["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _update_success_policy(self, state: Dict[str, Any], result: Dict[str, Any], event: Dict[str, Any]) -> None:
        patch = f"成功样本 {event.get('case_id')}: 工具链 {' -> '.join(event.get('tools_used', [])[:6])} 命中 {event.get('ground_truth_service')}。"
        patches = state["prompt_engine"].setdefault("learned_patches", [])
        if patch not in patches:
            patches.append(patch)
        state["prompt_engine"]["learned_patches"] = patches[-60:]
        state["prompt_engine"]["version"] = int(state["prompt_engine"].get("version", 1)) + 1
        context_patch = f"成功样本 {event.get('case_id')}: 上下文优先保留 {event.get('ground_truth_service')} 的直接指标/日志证据和 {' -> '.join(event.get('tools_used', [])[:4])} 工具 artifact diff。"
        context_patches = state["context_engine"].setdefault("learned_context_patches", [])
        if context_patch not in context_patches:
            context_patches.append(context_patch)
        state["context_engine"]["learned_context_patches"] = context_patches[-60:]
        semantic = {
            "trigger": f"{result.get('source')} / {result.get('case_name') or result.get('case_id')}",
            "learned_policy": "Reuse direct evidence, selected tool summaries, and dependency direction before ranking.",
            "tools": event.get("tools_used", []),
            "root_cause_service": event.get("ground_truth_service"),
            "created_at": event["timestamp"],
        }
        memory = state["memory_engine"]
        memory.setdefault("semantic_memory", []).append(semantic)
        memory["semantic_memory"] = memory["semantic_memory"][-50:]
        memory.setdefault("long_term_memory", []).append(semantic)
        memory["long_term_memory"] = memory["long_term_memory"][-50:]
        state["lifelong_learning"].setdefault("success_patterns", []).append({"case_id": event.get("case_id"), "pattern": patch, "timestamp": event["timestamp"]})
        state["lifelong_learning"]["success_patterns"] = state["lifelong_learning"]["success_patterns"][-60:]

    def _update_failure_policy(self, state: Dict[str, Any], result: Dict[str, Any], event: Dict[str, Any]) -> None:
        rule = f"失败样本 {event.get('case_id')}: Top1={event.get('top_candidate')}，GT={event.get('ground_truth_service')}；下一轮必须比较直接证据、传播方向和受害服务特征。"
        rules = state["prompt_engine"].setdefault("failure_contrast_rules", [])
        if rule not in rules:
            rules.append(rule)
        state["prompt_engine"]["failure_contrast_rules"] = rules[-60:]
        state["prompt_engine"]["version"] = int(state["prompt_engine"].get("version", 1)) + 1
        context_patch = f"失败样本 {event.get('case_id')}: 下一轮上下文必须增加 Top1={event.get('top_candidate')} 与 GT={event.get('ground_truth_service')} 的反事实对比，不允许只按高异常分排序。"
        context_patches = state["context_engine"].setdefault("learned_context_patches", [])
        if context_patch not in context_patches:
            context_patches.append(context_patch)
        state["context_engine"]["learned_context_patches"] = context_patches[-60:]
        failure = {
            "case_id": event.get("case_id"),
            "source": event.get("source"),
            "wrong_top": event.get("top_candidate"),
            "ground_truth": event.get("ground_truth_service"),
            "lesson": rule,
            "tools_used": event.get("tools_used", []),
            "created_at": event["timestamp"],
        }
        state["memory_engine"].setdefault("failure_memory", []).append(failure)
        state["memory_engine"]["failure_memory"] = state["memory_engine"]["failure_memory"][-60:]
        state["lifelong_learning"].setdefault("negative_trajectories", []).append(failure)
        state["lifelong_learning"]["negative_trajectories"] = state["lifelong_learning"]["negative_trajectories"][-60:]

    def _summarize_modalities(self, detail: Dict[str, Any]) -> Dict[str, Any]:
        metrics = detail.get("metrics") or {}
        logs = detail.get("logs") or {}
        traces = detail.get("traces") or {}
        alerts = detail.get("alerts") or {}
        return {
            "logs": {"count": len(logs.get("entries", []) or []), "sample": (logs.get("entries", []) or [])[:2]},
            "traces": {"count": len(traces.get("spans", []) or traces.get("traces", []) or []), "sample": (traces.get("spans", []) or traces.get("traces", []) or [])[:2]},
            "metrics": {"summary_count": len(metrics.get("series_summary", []) or []), "raw_count": len(metrics.get("raw_series", []) or []), "sample": (metrics.get("series_summary", []) or metrics.get("raw_series", []) or [])[:3]},
            "alerts": {"count": alerts.get("alert_count", len(alerts.get("alerts", []) or [])), "sample": (alerts.get("alerts", []) or [])[:2]},
        }

    def _data_availability(self, detail: Dict[str, Any]) -> Dict[str, Any]:
        metrics = detail.get("metrics") or {}
        logs = detail.get("logs") or {}
        alerts = detail.get("alerts") or {}
        graph = detail.get("service_graph") or {}
        metric_columns = detail.get("metric_columns") or []
        services = graph.get("services") or detail.get("service_inventory") or []
        edges = graph.get("edges") or []
        has_metrics = bool(metrics.get("series_summary") or metrics.get("raw_series") or metric_columns)
        has_logs = bool(logs.get("entries") or logs.get("total_entries"))
        has_alerts = bool(alerts.get("alerts") or alerts.get("alert_count"))
        has_graph = bool(services and edges)
        return {
            "has_metrics": has_metrics,
            "has_logs": has_logs,
            "has_alerts": has_alerts,
            "has_graph": has_graph,
            "has_metric_columns": bool(metric_columns),
            "service_count": len(services),
            "edge_count": len(edges),
            "modality_count": sum([has_metrics, has_logs, has_alerts, has_graph]),
        }

    def _selection_reason(self, name: str, plan: List[Dict[str, Any]]) -> str:
        for item in plan:
            if item.get("tool") == name:
                return item.get("reason", "")
        return ""

    def _recompute_metrics(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        runs = [e for e in events if e.get("case_id")]
        if not runs:
            return {"runs": 0, "top1_success_rate": 0, "avg_mrr": 0, "llm_usage_rate": 0}
        return {
            "runs": len(runs),
            "top1_success_rate": round(sum(1 for e in runs if e.get("hit_at_1")) / len(runs), 4),
            "avg_mrr": round(sum(float(e.get("mrr") or 0) for e in runs) / len(runs), 4),
            "llm_usage_rate": round(sum(1 for e in runs if e.get("llm_used")) / len(runs), 4),
        }

    @staticmethod
    def _canonical_tool_name(name: str) -> str:
        value = str(name or "").replace("_", "").replace("-", "").lower()
        mapping = {
            "opsaug": "OpsAug",
            "drainmcp": "DrainMCP",
            "kpifailure": "KPIFailure",
            "dynamicevolutionarysystem": "DynamicEvolutionarySystem",
            "dynamicevolution": "DynamicEvolutionarySystem",
            "opskb": "OpsKB",
            "promcopilot": "PromCopilot",
        }
        return mapping.get(value, "")

    def _with_expected_effects(self, plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ordered = []
        for item in plan:
            new_item = dict(item)
            new_item.setdefault("expected_effect", self._expected_tool_effect(new_item.get("tool")))
            ordered.append(new_item)
        return ordered

    @staticmethod
    def _expected_tool_effect(tool: str) -> str:
        return {
            "OpsAug": "把日志、指标、告警、拓扑等多模态信号压缩成跨模态异常摘要，降低后续模型上下文噪声。",
            "DrainMCP": "从原始日志中抽取模板、异常频次和疑似服务，确认故障是否有直接日志证据。",
            "KPIFailure": "对 CPU、内存、延迟、错误率等 KPI 计算异常强度，形成可排序的指标证据。",
            "DynamicEvolutionarySystem": "分析故障从注入点到业务、运行时、数据平面的传播路径和系统级影响。",
            "OpsKB": "检索架构依赖和运维知识，约束模型不要把受害服务误判为根因。",
            "PromCopilot": "生成可验证的 PromQL 查询或指标解释，用于复核关键服务指标。",
        }.get(str(tool or ""), "输出结构化摘要、证据指针和 artifact diff，供下一个 Agent/模型使用。")

    @staticmethod
    def tool_output_contract(tool: str) -> str:
        return {
            "OpsAug": "multimodal summary + candidate signals",
            "DrainMCP": "log templates + anomaly patterns + service suspicion",
            "KPIFailure": "metric anomalies + service score + KPI evidence",
            "DynamicEvolutionarySystem": "system propagation and bottleneck actions",
            "OpsKB": "knowledge constraints and topology context",
            "PromCopilot": "PromQL validation query and metric explanation",
        }.get(tool, "summary + evidence pointers")
