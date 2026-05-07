# -*- coding: utf-8 -*-
"""RCA Orchestrator — Real root cause analysis with LLM + tool evidence.

Key improvements over v1:
1. LLM receives structured tool outputs and MUST reason about causal chains
2. Built-in fallback uses dependency-aware causal analysis (not just counting)
3. HONEST evaluation: the injected service may NOT be ranked #1 if evidence is ambiguous
4. Creates genuine failure cases where complex fault propagation causes misses
"""

from __future__ import annotations

import json
import random
import re
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from .base_data_source import BaseDataSource
from .drain_mcp_adapter import DrainMCPAdapter
from .dynamic_evolutionary_adapter import DynamicEvolutionarySystemAdapter
from .kpi_failure_adapter import KPIFailureAdapter
from .opskb_adapter import OpsKBAdapter
from .promcopilot_adapter import PromCopilotAdapter
from .langchain_rca_multiagent import LangChainRCAMultiAgent
from .self_evolution import SelfEvolution
from .unified_opsaug_adapter import UnifiedOpsAugAdapter


class RcaOrchestrator:
    """End-to-end RCA pipeline with genuine LLM reasoning."""

    TOOLS = [
        "OpsAug", "DrainMCP", "KPIFailure",
        "DynamicEvolutionarySystem", "OpsKB", "PromCopilot",
    ]

    def __init__(
        self, data_source: BaseDataSource,
        llm_client=None, llm_config=None, llm_status: Optional[Dict[str, Any]] = None,
    ):
        self.data_source = data_source
        self.llm_client = llm_client
        self.llm_config = llm_config
        self.llm_status = llm_status or {}
        self._last_llm_error = ""
        self._last_llm_raw_preview = ""
        self.opsaug = UnifiedOpsAugAdapter(data_source)
        self.drain_mcp = DrainMCPAdapter(data_source)
        self.kpi_failure = KPIFailureAdapter(data_source)
        self.dynamic_evolution = DynamicEvolutionarySystemAdapter(data_source)
        self.opskb = OpsKBAdapter(data_source)
        self.promcopilot = PromCopilotAdapter(data_source)
        self.evolution = SelfEvolution()
        self.rca_agent = LangChainRCAMultiAgent()

    def run_pipeline(self, case_id: str, run_tools: Optional[List[str]] = None) -> Dict[str, Any]:
        start_time = time.time()
        result = {
            "case_id": case_id, "source": self.data_source.name,
            "source_type": self.data_source.source_type,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "steps": {}, "tool_plan": [], "rca_result": {}, "evaluation": {},
            "duration_s": 0, "status": "running",
        }
        try:
            # Step 1: Load evidence
            t0 = time.time()
            detail = self.data_source.get_case_detail(case_id)
            result["case_name"] = detail.get("case_name", case_id)
            result["ground_truth"] = detail.get("root_cause_ground_truth", "")
            result["steps"]["evidence_loading"] = {
                "duration_s": round(time.time() - t0, 2), "status": "ok",
                "explanation": "加载案例的日志、指标、告警、K8s 状态和服务依赖拓扑，作为后续工具共用的现场证据。",
                "has_metrics": bool(detail.get("metrics", {}).get("series_summary")),
                "has_logs": bool(detail.get("logs", {}).get("entries")),
                "service_count": len(detail.get("service_graph", {}).get("services", [])),
            }
            agent_guidance = self.evolution.get_runtime_guidance(
                self.data_source.name,
                str(detail.get("case_name") or detail.get("fault_category") or ""),
            )
            result["agent_guidance"] = agent_guidance
            result["raw_data_summary"] = self._build_raw_data_summary(detail)
            agent_task = self.rca_agent.prepare_rca_task(
                detail, agent_guidance, run_tools, self.TOOLS, result["raw_data_summary"]
            )
            result["rca_agent_context"] = agent_task["context_package"]
            result["rca_agent_prompt_guidance"] = agent_task["prompt_guidance"]
            result["rca_graph"] = agent_task["graph"]
            result["tool_plan"] = agent_task["tool_plan"]

            tool_specs = {
                "OpsAug": {
                    "step_id": "opsaug",
                    "runner": lambda: self.opsaug.summarize_case(case_id),
                    "explanation": "OpsAug 汇总五模态证据，先给出跨日志、指标、告警、K8s 和拓扑的一致异常信号。",
                    "collect_evidence": False,
                },
                "DrainMCP": {
                    "step_id": "drainmcp",
                    "runner": lambda: self.drain_mcp.analyze(case_id),
                    "explanation": self._tool_explanation("DrainMCP"),
                },
                "KPIFailure": {
                    "step_id": "kpifailure",
                    "runner": lambda: self.kpi_failure.analyze(case_id),
                    "explanation": self._tool_explanation("KPIFailure"),
                },
                "DynamicEvolutionarySystem": {
                    "step_id": "dynamicevolutionarysystem",
                    "runner": lambda: self.dynamic_evolution.analyze(case_id),
                    "explanation": self._tool_explanation("DynamicEvolutionarySystem"),
                },
                "OpsKB": {
                    "step_id": "opskb",
                    "runner": lambda: self.opskb.query_knowledge(case_id),
                    "explanation": self._tool_explanation("OpsKB"),
                },
                "PromCopilot": {
                    "step_id": "promcopilot",
                    "runner": lambda: self.promcopilot.generate_for_case(case_id, ""),
                    "explanation": self._tool_explanation("PromCopilot"),
                },
            }
            agent_exec = self.rca_agent.execute_toolchain(
                agent_task,
                tool_specs,
                lambda name, payload, status, artifact: self._build_tool_data_flow(name, detail, payload, status, artifact),
                self._summarize_tool_result,
            )
            result["steps"].update(agent_exec["steps"])
            result["opsaug"] = agent_exec["outputs"].get("OpsAug", {})
            tool_evidence = agent_exec["evidence"]
            current_artifact = agent_exec["final_artifact"]
            result["agent_execution"] = {
                "task_id": agent_task["task_id"],
                "graph_id": agent_task["graph"].get("graph_id"),
                "selected_tools": agent_task["selected_tools"],
                "execution_trace": agent_exec["execution_trace"],
                "final_artifact_stage": current_artifact.get("stage"),
                "graph": agent_task["graph"],
            }

            # Step 4: LLM RCA (or built-in causal analysis)
            t0 = time.time()
            rca_result = self._run_llm_rca(case_id, detail, result, tool_evidence)
            result["_tool_evidence"] = tool_evidence  # for frontend display
            result["llm_input_summary"] = self._build_llm_input_summary(detail, result, tool_evidence)
            result["steps"]["llm_rca"] = {
                "duration_s": round(time.time() - t0, 2), "status": "ok",
                "explanation": "大模型或内置因果推理器会读取前面所有工具结论，并结合依赖图推断 Top-K 根因候选。",
                "summary": self._summarize_tool_result(rca_result),
                "selection_reason": "最终推理节点固定触发，但它只接收本轮已选工具的结果。",
                "data_flow": {
                    "input_modalities": ["tool_evidence", "topology", "multiagent_memory", "raw_data_summary"],
                    "before_data": current_artifact,
                    "input_sample": result["llm_input_summary"],
                    "transform": "把已筛选工具证据、传播拓扑、多 Agent 记忆和关键原始指标压缩成 LLM RCA 上下文。",
                    "after_data": {
                        "stage": "llm_ranked_candidates",
                        "data": {
                            "model": rca_result.get("model"),
                            "llm_used": rca_result.get("llm_used"),
                            "fallback_used": rca_result.get("fallback_used"),
                            "candidates": (rca_result.get("parsed_candidates") or [])[:5],
                        },
                    },
                    "changed_summary": "LangChain 多 Agent 模块将上一轮工具产物压缩成模型上下文，并把模型输出转为 Top-K 候选列表。",
                    "output_sample": {
                        "model": rca_result.get("model"),
                        "llm_used": rca_result.get("llm_used"),
                        "candidates": (rca_result.get("parsed_candidates") or [])[:3],
                    },
                },
            }
            result["rca_result"] = rca_result

            # Step 5: Evaluation
            t0 = time.time()
            result["evaluation"] = self._evaluate_acc_at_k(
                rca_result, result["ground_truth"], detail
            )
            result["steps"]["evaluation"] = {"duration_s": round(time.time() - t0, 2), "status": "ok"}
            result["status"] = "completed"

            # SelfEvolution
            try:
                self.evolution.record_run(result)
            except Exception:
                pass
            try:
                result["rca_agent_update"] = self.rca_agent.update_from_rca(result)
            except Exception as e:
                result["rca_agent_update_error"] = str(e)
            result["multiagent_diagnosis"] = self._build_multiagent_diagnosis(
                detail=detail,
                result=result,
                agent_task=agent_task,
                agent_exec=agent_exec,
                rca_result=rca_result,
                evaluation=result["evaluation"],
                update_event=result.get("rca_agent_update", {}),
            )
            result["evolution_insights"] = self.evolution.get_insights()

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)

        result["duration_s"] = round(time.time() - start_time, 2)
        return result

    def _build_multiagent_diagnosis(
        self,
        detail: Dict[str, Any],
        result: Dict[str, Any],
        agent_task: Dict[str, Any],
        agent_exec: Dict[str, Any],
        rca_result: Dict[str, Any],
        evaluation: Dict[str, Any],
        update_event: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return the RCA execution as an explicit agent-to-agent handoff chain."""
        graph = agent_task.get("graph") or {}
        context = agent_task.get("context_package") or {}
        prompt_guidance = agent_task.get("prompt_guidance") or {}
        memory = context.get("memory_capsules") or {}
        tool_plan = agent_task.get("tool_plan") or []
        selected_tools = agent_task.get("selected_tools") or []
        raw = result.get("raw_data_summary") or self._build_raw_data_summary(detail)
        tool_steps = agent_exec.get("steps") or {}
        trace = agent_exec.get("execution_trace") or []
        candidates = rca_result.get("parsed_candidates") or rca_result.get("candidates") or []
        top = candidates[0] if candidates else {}
        skipped = [item for item in tool_plan if not item.get("selected")]
        selected = [item for item in tool_plan if item.get("selected")]
        llm_status = rca_result.get("llm_status") or {}
        update_event = update_event or {}
        services = detail.get("service_graph", {}).get("services", []) or []
        edges = detail.get("service_graph", {}).get("edges", []) or []

        def compact(value: Any, limit: int = 900) -> Any:
            text = json.dumps(value, ensure_ascii=False, default=str)
            if len(text) <= limit:
                return value
            return {"preview": text[:limit] + "...", "truncated": True}

        def step(
            agent_id: str,
            name: str,
            role: str,
            title: str,
            input_artifact: str,
            analysis: str,
            output_title: str,
            output: Dict[str, Any],
            handoff_to: str,
            handoff_payload: List[str],
            logs: List[str],
            status: str = "completed",
            duration_s: float = 0.1,
            subtasks: Optional[List[Dict[str, Any]]] = None,
        ) -> Dict[str, Any]:
            return {
                "agent_id": agent_id,
                "name": name,
                "role": role,
                "title": title,
                "status": status,
                "duration_s": duration_s,
                "input_artifact": input_artifact,
                "analysis": analysis,
                "output_title": output_title,
                "output": compact(output),
                "handoff_to": handoff_to,
                "handoff_payload": handoff_payload,
                "logs": logs,
                "subtasks": subtasks or [],
            }

        tool_subtasks: List[Dict[str, Any]] = []
        for item in tool_plan:
            tool = item.get("tool")
            if not tool:
                continue
            step_id = self._tool_step_id(tool)
            tool_step = tool_steps.get(step_id, {})
            flow = tool_step.get("data_flow") or {}
            tool_subtasks.append({
                "tool": tool,
                "status": tool_step.get("status", "selected" if item.get("selected") else "skipped"),
                "reason": item.get("reason", ""),
                "summary": tool_step.get("summary", ""),
                "before_stage": (flow.get("before_data") or {}).get("stage"),
                "after_stage": (flow.get("after_data") or {}).get("stage"),
                "changed_summary": flow.get("changed_summary", ""),
            })

        context_budget = context.get("context_contract", {}).get("budget", {})
        agent_steps = [
            step(
                "sop_agent",
                "SOP 智能体",
                "Runbook / SOP Orchestrator",
                "建立 RCA 作战契约",
                "fault_injection_case",
                "把故障注入案例转成可执行任务：明确目标、成功标准、停止条件和人工确认点。",
                "sop_contract",
                {
                    "case": detail.get("case_name") or detail.get("case_id"),
                    "ground_truth_visible_for_eval_only": bool(result.get("ground_truth")),
                    "success_criteria": context.get("context_contract", {}).get("success_criteria", []),
                    "human_gates": ["工具预案确认", "逐 Agent 执行确认", "RCA 结果采纳确认"],
                },
                "context_prompt_agent",
                ["case metadata", "success criteria", "stop conditions", "human gates"],
                [
                    f"读取案例 {detail.get('case_id') or result.get('case_id')}",
                    f"服务 {len(services)} 个，依赖边 {len(edges)} 条",
                    "禁止直接把 ground truth 喂给诊断模型，只用于最后评估。",
                ],
                duration_s=float(result.get("steps", {}).get("evidence_loading", {}).get("duration_s", 0.1) or 0.1),
            ),
            step(
                "context_prompt_agent",
                "Prompt/上下文管理智能体",
                "Context and Prompt Manager",
                "压缩上下文并生成 Prompt 包",
                "raw log / trace / metric / alert / topology",
                "按模态分层组织原始数据，过滤低信号内容，只保留摘要、关键样本、拓扑和 Prompt/上下文补丁。",
                "context_contract + prompt_pack",
                {
                    "budget": context_budget,
                    "modalities": context.get("context_layers", {}).get("modalities", {}),
                    "prompt_version": prompt_guidance.get("prompt_version"),
                    "active_template": prompt_guidance.get("active_template", {}),
                    "base_rules": prompt_guidance.get("base_rules", [])[:5],
                },
                "memory_agent",
                ["context contract", "prompt version", "filtered modality summary"],
                [
                    f"log={raw.get('logs', {}).get('count', 0)} trace={raw.get('traces', {}).get('count', 0)} metric={raw.get('metrics', {}).get('count', 0)} alert={raw.get('alerts', {}).get('count', 0)}",
                    f"Prompt version v{prompt_guidance.get('prompt_version', 1)}",
                    "输出前先压缩证据，避免大模型被原始数据淹没。",
                ],
            ),
            step(
                "memory_agent",
                "记忆检索智能体",
                "Memory Retriever",
                "检索相似案例与失败反例",
                "context_contract + case keys",
                "从短期记忆、语义记忆和失败轨迹中提取当前案例可复用策略，并作为诊断反事实约束。",
                "memory_capsules",
                {
                    "short_term": memory.get("short_term", {}),
                    "semantic": memory.get("semantic", [])[:4],
                    "failures": memory.get("failures", [])[:4],
                    "retrieval_policy": memory.get("retrieval_policy", {}),
                },
                "tool_decision_agent",
                ["semantic memory", "failure counterexamples", "retrieval policy"],
                [
                    f"命中语义记忆 {len(memory.get('semantic') or [])} 条",
                    f"命中失败轨迹 {len(memory.get('failures') or [])} 条",
                    "失败记忆会约束后续诊断，避免把高异常受害服务误判为根因。",
                ],
            ),
            step(
                "tool_decision_agent",
                "工具调用决策智能体",
                "Adaptive Tool Router",
                "选择本轮工具而不是全量调用",
                "context + memory + tool reward",
                f"从 {len(tool_plan)} 个内置工具中选择 {len(selected_tools)} 个进入执行链，未选择工具保留原因但不进入模型上下文。",
                "ordered_tool_plan",
                {
                    "selected_tools": selected_tools,
                    "selected_reasons": [
                        {"tool": item.get("tool"), "reason": item.get("reason"), "reward": item.get("learned_reward")}
                        for item in selected
                    ],
                    "skipped_tools": [
                        {"tool": item.get("tool"), "reason": item.get("reason"), "reward": item.get("learned_reward")}
                        for item in skipped
                    ],
                },
                "evidence_agent",
                ["ordered selected tools", "skipped tool reasons", "tool output contract"],
                [
                    "按数据模态、拓扑规模、历史 reward 和上下文预算路由工具。",
                    "低收益或无输入数据的工具不会被调用。",
                    "每个被选工具必须输出摘要、证据和 artifact diff。",
                ],
            ),
            step(
                "evidence_agent",
                "多模态证据分析智能体",
                "Log / Trace / Metric Evidence Analyst",
                "执行工具并生成证据交接件",
                "ordered_tool_plan + raw evidence",
                "按工具调用决策逐个执行工具；每个工具都把上一阶段数据转成新 artifact，并记录 before/after 差异。",
                "tool_evidence_artifacts",
                {
                    "executed_tools": [
                        {"tool": item.get("tool"), "status": item.get("status"), "after_stage": item.get("after_stage")}
                        for item in tool_subtasks
                    ],
                    "evidence_count": len(agent_exec.get("evidence") or []),
                    "final_artifact_stage": (agent_exec.get("final_artifact") or {}).get("stage"),
                    "trace": trace[-8:],
                },
                "diagnosis_agent",
                ["tool evidence summaries", "artifact diffs", "final evidence artifact"],
                [
                    f"执行/跳过工具节点 {len(tool_subtasks)} 个",
                    f"可交给诊断智能体的证据包 {len(agent_exec.get('evidence') or [])} 个",
                    f"最终 artifact: {(agent_exec.get('final_artifact') or {}).get('stage') or 'raw_fault_data'}",
                ],
                subtasks=tool_subtasks,
            ),
            step(
                "diagnosis_agent",
                "诊断智能体",
                "LLM RCA Diagnostician",
                "综合证据生成 Top-K 根因",
                "tool evidence + topology + prompt pack + memory",
                "使用大模型作为诊断智能体，读取上游 Agent 的证据交接件、拓扑传播和记忆约束，输出带证据理由的根因候选。",
                "ranked_root_cause_candidates",
                {
                    "llm_used": bool(rca_result.get("llm_used")),
                    "fallback_used": bool(rca_result.get("fallback_used")),
                    "model": rca_result.get("model"),
                    "llm_status": llm_status,
                    "top_candidates": candidates[:5],
                },
                "critic_learning_agent",
                ["Top-K candidates", "LLM status", "candidate evidence reasons"],
                [
                    f"LLM used={bool(rca_result.get('llm_used'))}, fallback={bool(rca_result.get('fallback_used'))}",
                    f"Top1={top.get('service', '-')}, score={top.get('score', '-')}",
                    f"候选数量 {len(candidates)}",
                ],
                duration_s=float(result.get("steps", {}).get("llm_rca", {}).get("duration_s", 0.1) or 0.1),
            ),
            step(
                "critic_learning_agent",
                "评估/终身学习智能体",
                "Independent Evaluator and Lifelong Learner",
                "评估命中并写入学习记忆",
                "ranked candidates + ground truth for evaluation",
                "独立计算 ACC@K/MRR；成功样本写入策略记忆，失败样本写入负轨迹并反向更新 Prompt、上下文过滤和工具 reward。",
                "memory_prompt_tool_updates",
                {
                    "evaluation": evaluation,
                    "update_event": update_event,
                    "agent_update_error": result.get("rca_agent_update_error", ""),
                },
                "END",
                ["ACC@K", "MRR", "prompt/context/tool reward update"],
                [
                    f"ACC@1={evaluation.get('ACC@1', '-')}, MRR={evaluation.get('MRR', '-')}",
                    f"GroundTruth={evaluation.get('ground_truth_service', '-')}, Top1={evaluation.get('top_candidate', '-')}",
                    f"学习事件 {update_event.get('id', 'not-recorded')}",
                ],
            ),
        ]
        for idx, item in enumerate(agent_steps):
            item["order"] = idx + 1
            item["next_agent_id"] = agent_steps[idx + 1]["agent_id"] if idx + 1 < len(agent_steps) else "END"

        return {
            "mode": "agent_handoff_rca",
            "framework": (graph or {}).get("framework", "langchain_multiagent"),
            "graph_id": graph.get("graph_id"),
            "process": "sop_to_context_to_memory_to_tool_decision_to_evidence_to_diagnosis_to_learning",
            "steps": agent_steps,
            "handoffs": [
                {
                    "from": item["agent_id"],
                    "to": item["next_agent_id"],
                    "payload": item["handoff_payload"],
                }
                for item in agent_steps
                if item.get("next_agent_id") != "END"
            ],
            "final_root_cause": top.get("service", ""),
            "final_confidence": top.get("score", 0),
            "final_reason": top.get("reason", ""),
        }

    @staticmethod
    def _tool_step_id(tool: str) -> str:
        return str(tool or "").replace("_", "").replace("-", "").lower()

    def _build_raw_data_summary(self, detail: Dict[str, Any]) -> Dict[str, Any]:
        metrics = detail.get("metrics", {}) or {}
        logs = detail.get("logs", {}) or {}
        traces = detail.get("traces", {}) or {}
        alerts = detail.get("alerts", {}) or {}
        metric_rows = metrics.get("series_summary") or metrics.get("raw_series") or []
        top_metrics = sorted(
            [m for m in metric_rows if isinstance(m, dict)],
            key=self._numeric_signal,
            reverse=True,
        )[:6]
        return {
            "logs": {
                "count": len(logs.get("entries", []) or []),
                "sample": (logs.get("entries", []) or [])[:4],
            },
            "traces": {
                "count": len(traces.get("spans", []) or traces.get("traces", []) or []),
                "sample": (traces.get("spans", []) or traces.get("traces", []) or [])[:4],
            },
            "metrics": {
                "count": len(metric_rows),
                "top_values": top_metrics,
            },
            "alerts": {
                "count": alerts.get("alert_count", len(alerts.get("alerts", []) or [])),
                "sample": (alerts.get("alerts", []) or [])[:4],
            },
        }

    @staticmethod
    def _numeric_signal(item: Dict[str, Any]) -> float:
        for key in ("max", "value", "mean", "range"):
            try:
                return float(item.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
        return 0.0

    def _build_tool_data_flow(
        self,
        tool: str,
        detail: Dict[str, Any],
        output: Dict[str, Any],
        status: str,
        previous_artifact: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        modality_map = {
            "OpsAug": ["logs", "traces", "metrics", "alerts", "topology"],
            "DrainMCP": ["logs"],
            "KPIFailure": ["metrics"],
            "DynamicEvolutionarySystem": ["topology", "metrics"],
            "OpsKB": ["topology", "service_inventory"],
            "PromCopilot": ["metrics", "metric_columns"],
        }
        raw = self._build_raw_data_summary(detail)
        selected = modality_map.get(tool, ["case_context"])
        after_data = self._next_artifact(tool, output) if status == "ok" else {
            "stage": f"{tool}_skipped_or_failed",
            "data": {"status": status},
            "description": "该工具未产生新的处理后数据，后续步骤沿用上一阶段产物。",
        }
        before_data = previous_artifact or {
            "stage": "raw_fault_data",
            "data": raw,
            "description": "原始 log / trace / metric / alert 数据。",
        }
        return {
            "input_modalities": selected,
            "before_data": before_data,
            "input_sample": {name: raw.get(name, detail.get(name)) for name in selected if name in raw or name in detail},
            "transform": {
                "OpsAug": "多模态提取、过滤、摘要，形成跨模态异常信号。",
                "DrainMCP": "日志模板聚类，压缩重复日志并突出异常模板。",
                "KPIFailure": "指标异常排序，提取均值、峰值、范围等可解释 KPI。",
                "DynamicEvolutionarySystem": "从拓扑和异常点估计故障传播及演化动作。",
                "OpsKB": "把服务依赖和历史知识压缩成架构背景。",
                "PromCopilot": "从指标列和服务名生成可执行 PromQL 验证语句。",
            }.get(tool, "把输入证据转换为 RCA 可消费的结构化摘要。"),
            "after_data": after_data,
            "changed_summary": self._describe_data_change(tool, before_data, after_data, status),
            "output_sample": self._compact_output(output) if status == "ok" else {"status": status},
        }

    def _next_artifact(self, tool: str, output: Dict[str, Any]) -> Dict[str, Any]:
        compact = self._compact_output(output)
        return {
            "stage": f"{tool}_processed_data",
            "data": compact,
            "description": {
                "OpsAug": "原始多模态数据被压缩为跨模态异常提示和候选线索。",
                "DrainMCP": "原始日志被模板化，保留异常模板、服务分布和日志侧候选。",
                "KPIFailure": "原始指标被转为异常点、服务评分和指标侧候选。",
                "DynamicEvolutionarySystem": "拓扑和指标被转为传播/瓶颈/演化动作。",
                "OpsKB": "服务拓扑被转为架构知识和规则约束。",
                "PromCopilot": "指标列被转为验证查询和可执行 PromQL。",
            }.get(tool, "工具输出被压缩成后续多 Agent/模型可消费的结构化产物。"),
        }

    def _describe_data_change(self, tool: str, before_data: Dict[str, Any], after_data: Dict[str, Any], status: str) -> str:
        if status != "ok":
            return "本步未生成新产物；后续步骤继续沿用上一阶段数据。"
        return {
            "OpsAug": "从原始 log/trace/metric/alert 变为跨模态摘要、模态提示和根因候选线索。",
            "DrainMCP": "从上一阶段数据中抽取日志模板，减少重复日志，突出异常服务和日志候选。",
            "KPIFailure": "把指标样本变成异常类型、严重度和服务级得分，方便与日志候选对照。",
            "DynamicEvolutionarySystem": "把服务/系统状态转换为传播路径、瓶颈和可演化动作。",
            "OpsKB": "把拓扑上下文转换为知识约束，限制模型不要只凭高频异常判断。",
            "PromCopilot": "把指标列和服务名转换为验证查询，为下一步模型分析提供可复核依据。",
        }.get(tool, f"{before_data.get('stage', 'previous')} -> {after_data.get('stage', 'processed')}")

    def _compact_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(output, dict):
            return {"value": str(output)[:300]}
        keys = [
            "summary", "fault_warnings", "fault_localizations", "root_cause_candidates",
            "root_cause_diagnosis", "evolution_actions", "promql", "queries", "parsed_candidates",
        ]
        compact = {}
        for key in keys:
            if key in output:
                val = output[key]
                compact[key] = val[:3] if isinstance(val, list) else val
        return compact or {"keys": list(output.keys())[:8]}

    def _build_llm_input_summary(self, detail: Dict[str, Any], ctx: Dict[str, Any], tool_evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
        raw = self._build_raw_data_summary(detail)
        metrics = raw["metrics"].get("top_values", [])[:5]
        return {
            "case_id": detail.get("case_id"),
            "case_name": detail.get("case_name"),
            "raw_fault_data_given_to_model": {
                "log_count": raw["logs"]["count"],
                "trace_count": raw["traces"]["count"],
                "metric_count": raw["metrics"]["count"],
                "alert_count": raw["alerts"]["count"],
                "top_metric_values": metrics,
                "log_samples": raw["logs"]["sample"][:3],
                "trace_samples": raw["traces"]["sample"][:3],
            },
            "selected_tools": [
                item.get("tool") for item in ctx.get("tool_plan", [])
                if item.get("selected")
            ],
            "tool_evidence_count": len(tool_evidence),
            "multiagent_memory": {
                "iterations": (ctx.get("agent_guidance") or {}).get("iterations", 0),
                "policy_version": (ctx.get("agent_guidance") or {}).get("policy_version", 1),
            },
            "agent_handoff_context": {
                "sop_agent": "定义 RCA 目标、成功标准、停止条件和人工确认门。",
                "context_prompt_agent": "只传递分层摘要、关键样本、Prompt 版本和上下文预算。",
                "memory_agent": "检索相似成功策略与失败反例，作为反事实约束。",
                "tool_decision_agent": [
                    {
                        "tool": item.get("tool"),
                        "selected": bool(item.get("selected")),
                        "reason": item.get("reason"),
                    }
                    for item in ctx.get("tool_plan", [])
                ],
                "evidence_agent": f"已生成 {len(tool_evidence)} 个工具证据包，供诊断智能体消费。",
            },
            "multiagent_context_contract": (ctx.get("rca_agent_context") or {}).get("context_contract", {}),
            "multiagent_memory_capsules": (ctx.get("rca_agent_context") or {}).get("memory_capsules", {}),
            "rca_graph": {
                "graph_id": (ctx.get("rca_graph") or {}).get("graph_id"),
                "process": (ctx.get("rca_graph") or {}).get("process"),
                "agents": [
                    {"id": a.get("id"), "role": a.get("role")}
                    for a in (ctx.get("rca_graph") or {}).get("agents", [])
                ],
                "tasks": [
                    {"id": t.get("id"), "agent": t.get("agent"), "tool": t.get("tool"), "status": t.get("status")}
                    for t in (ctx.get("rca_graph") or {}).get("tasks", [])
                ],
            },
        }

    def _select_tools(
        self,
        detail: Dict[str, Any],
        requested: Optional[List[str]],
        guidance: Optional[Dict[str, Any]] = None,
    ) -> tuple[List[str], List[Dict[str, Any]]]:
        """Select tools from data availability + learned policy.

        The design mirrors a self-improving agent loop: observe the case,
        retrieve learned tool preferences, then choose only tools that can add
        evidence for the current data shape.
        """
        if requested:
            selected = [self._canonical_tool_name(t) for t in requested if self._canonical_tool_name(t)]
            return selected, [
                {"tool": tool, "selected": True, "reason": "用户或上层流程显式指定本轮使用该工具。"}
                for tool in selected
            ]

        metrics = detail.get("metrics", {}) or {}
        logs = detail.get("logs", {}) or {}
        alerts = detail.get("alerts", {}) or {}
        graph = detail.get("service_graph", {}) or {}
        metric_columns = detail.get("metric_columns", []) or []
        services = graph.get("services", []) or detail.get("service_inventory", []) or []
        edges = graph.get("edges", []) or []

        has_metrics = bool(metrics.get("series_summary") or metrics.get("raw_series") or metric_columns)
        has_logs = bool(logs.get("entries") or logs.get("total_entries"))
        has_alerts = bool(alerts.get("alerts") or alerts.get("alert_count"))
        has_graph = bool(services and edges)
        multimodal_count = sum([has_metrics, has_logs, has_alerts, has_graph])

        weights = (guidance or {}).get("tool_weights", {})

        candidates = {
            "OpsAug": (
                multimodal_count >= 2,
                f"检测到 {multimodal_count} 类可用证据，适合先做多模态融合。",
                f"仅检测到 {multimodal_count} 类证据，多模态融合收益不足，本轮跳过。",
            ),
            "DrainMCP": (
                has_logs,
                "检测到日志数据，日志模板聚类可提供异常服务线索。",
                "未检测到有效日志条目，跳过日志模板分析。",
            ),
            "KPIFailure": (
                has_metrics,
                "检测到指标序列或指标列，KPI 异常检测可提供量化证据。",
                "未检测到指标序列或指标列，跳过 KPI 异常检测。",
            ),
            "DynamicEvolutionarySystem": (
                has_graph and (self.data_source.source_type == "dynamic" or len(edges) >= 8),
                "存在服务依赖拓扑，且案例可能涉及传播/瓶颈/副本调整，触发动态演化分析。",
                "依赖拓扑不足或不涉及动态演化场景，本轮跳过。",
            ),
            "OpsKB": (
                has_graph and len(services) >= 3,
                "存在服务清单和依赖关系，触发知识库检索补充架构背景。",
                "服务拓扑或服务清单不足，跳过知识库依赖检索。",
            ),
            "PromCopilot": (
                has_metrics and bool(metric_columns),
                "存在可查询指标列，触发 PromQL 生成用于后续验证。",
                "未检测到可查询指标列，跳过 PromQL 生成。",
            ),
        }

        selected = []
        plan = []
        for tool in self.TOOLS:
            ok, selected_reason, skipped_reason = candidates.get(tool, (False, "", "未匹配到有效数据触发条件。"))
            reason = selected_reason if ok else skipped_reason
            weight = weights.get(tool.lower()) or weights.get(tool) or {}
            score = float(weight.get("score", 0.5)) if isinstance(weight, dict) else 0.5
            if ok and tool in {"DynamicEvolutionarySystem", "OpsKB", "PromCopilot"} and score < 0.25:
                ok = False
                reason = f"该工具历史收益较低（学习权重 {score:.2f}），本轮跳过并保留核心证据工具。"
            if ok:
                selected.append(tool)
                if isinstance(weight, dict) and weight.get("runs", 0):
                    reason += f" 自进化权重 {score:.2f}，历史运行 {weight.get('runs', 0)} 次。"
            plan.append({"tool": tool, "selected": bool(ok), "reason": reason, "learned_weight": score})

        if not selected:
            selected = ["OpsAug"]
            plan = [{"tool": "OpsAug", "selected": True, "reason": "证据较少，启用最小多模态汇总作为兜底入口。"}]
        return selected, plan

    def _selection_reason(self, name: str, plan: List[Dict[str, Any]]) -> str:
        for item in plan:
            if item.get("tool") == name:
                return item.get("reason", "")
        return ""

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

    def _tool_explanation(self, name: str) -> str:
        return {
            "DrainMCP": "DrainMCP 将日志聚类成模板，寻找异常模板和高频错误，判断日志侧最可疑服务。",
            "KPIFailure": "KPIFailure 分析 CPU、内存、延迟、错误率等 KPI 异常，给出指标侧定位证据。",
            "DynamicEvolutionarySystem": "DynamicEvolutionarySystem 检查服务依赖、瓶颈和副本/拓扑调整空间，输出可演化动作。",
            "OpsKB": "OpsKB 检索运维知识和服务依赖背景，为根因判断提供规则与经验参考。",
            "PromCopilot": "PromCopilot 根据案例上下文生成 PromQL，说明下一步应如何查询验证指标。",
        }.get(name, "该工具提供根因分析链路中的一类证据。")

    def _summarize_tool_result(self, payload: Dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        if payload.get("summary"):
            return str(payload["summary"])[:500]
        locs = payload.get("fault_localizations") or payload.get("root_cause_candidates") or []
        if locs:
            parts = []
            for item in locs[:3]:
                if isinstance(item, dict):
                    service = item.get("service") or item.get("root_cause") or item.get("name") or "unknown"
                    reason = item.get("reason") or item.get("description") or item.get("evidence") or ""
                    parts.append(f"{service}: {reason}"[:180])
            if parts:
                return "；".join(parts)
        actions = payload.get("evolution_actions") or []
        if actions:
            return "；".join(str(a.get("recommendation", a))[:180] for a in actions[:3])
        cands = payload.get("parsed_candidates") or payload.get("candidates") or []
        if cands:
            return "；".join(f"#{c.get('rank', '?')} {c.get('service', '?')} score={c.get('score', 0)}" for c in cands[:3])
        return json.dumps(payload, ensure_ascii=False, default=str)[:500]

    def _run_llm_rca(self, case_id: str, detail: Dict, ctx: Dict, tool_evidence: List[Dict]) -> Dict[str, Any]:
        """Run LLM-based RCA using ALL tool evidence."""
        services = detail.get("service_graph", {}).get("services", [])
        edges = detail.get("service_graph", {}).get("edges", [])
        opsaug = ctx.get("opsaug", {})

        # Build structured evidence summary
        evidence_summary = []
        for te in tool_evidence:
            if not te:
                continue
            tool_name = te.get("tool", "unknown")
            if "fault_warnings" in te:
                warnings = te["fault_warnings"][:5]
                for w in warnings:
                    evidence_summary.append(f"[{tool_name}] {w.get('description', '')}")
            if "fault_localizations" in te:
                for loc in te["fault_localizations"][:5]:
                    evidence_summary.append(f"[{tool_name}] 定位: {loc.get('reason', '')}")
            if "root_cause_diagnosis" in te:
                diag = te["root_cause_diagnosis"]
                if isinstance(diag, dict) and diag.get("diagnosis"):
                    evidence_summary.append(f"[{tool_name}] 诊断: {diag['diagnosis'][:300]}")

        # Build dependency graph summary
        dep_summary = []
        for e in edges[:15]:
            dep_summary.append(f"{e.get('source','?')} → {e.get('target','?')} ({e.get('call_type','')})")

        # Build OpsAug hints
        opsaug_hints = []
        if opsaug:
            for mod_name in ["alerts", "logs", "metrics", "k8s_states"]:
                hints = opsaug.get("modalities", {}).get(mod_name, {}).get("hints", [])
                for h in hints[:3]:
                    opsaug_hints.append(f"[OpsAug/{mod_name}] {h}")

        # Try real LLM first
        llm_candidates = None
        llm_used = False
        llm_candidate_count = 0
        llm_status = {
            "requested": bool(self.llm_status.get("requested", True)),
            "configured": bool(self.llm_status.get("configured", self.llm_config is not None)),
            "available": bool(self.llm_client and self.llm_config),
            "attempted": False,
            "used": False,
            "response_received": False,
            "parsed_candidates": 0,
            "model": getattr(self.llm_config, "model", None) if self.llm_config else None,
            "base_url": getattr(self.llm_config, "base_url", None) if self.llm_config else None,
            "health": self.llm_status.get("health") or self.llm_status.get("initial_health"),
            "bootstrap": self.llm_status.get("bootstrap"),
            "error": self.llm_status.get("error", ""),
        }
        llm_input_summary = self._build_llm_input_summary(detail, ctx, tool_evidence)
        if self.llm_client and self.llm_config:
            try:
                self._last_llm_error = ""
                self._last_llm_raw_preview = ""
                llm_status["attempted"] = True
                llm_candidates = self._call_llm(
                    case_id, detail, evidence_summary, dep_summary, opsaug_hints,
                    services, ctx.get("agent_guidance", {}), llm_input_summary,
                    ctx.get("rca_agent_prompt_guidance", {})
                )
                llm_used = bool(self._last_llm_raw_preview)
                llm_candidate_count = len(llm_candidates or [])
                llm_status["used"] = llm_used
                llm_status["response_received"] = llm_used
                llm_status["parsed_candidates"] = llm_candidate_count
                if not llm_candidates:
                    llm_status["error"] = self._last_llm_error or "模型已调用，但返回内容未能解析为根因候选，已进入兜底因果分析。"
                    if self._last_llm_raw_preview:
                        llm_status["raw_preview"] = self._last_llm_raw_preview
            except Exception:
                llm_status["error"] = self._last_llm_error or "模型调用异常，已进入兜底因果分析。"
        elif not llm_status.get("error"):
            llm_status["error"] = "LLM 服务未就绪或未通过健康检查，未进入模型调用阶段。"

        # If LLM failed or unavailable, use built-in causal analysis
        fallback_used = not bool(llm_candidates)
        if fallback_used:
            llm_candidates = self._builtin_causal_rca(
                detail, tool_evidence, evidence_summary, dep_summary, services, edges
            )

        return {
            "model": (getattr(self.llm_config, "model", None) or "Qwen-0.6B") if llm_used else "builtin_causal",
            "llm_used": llm_used,
            "fallback_used": fallback_used,
            "llm_status": llm_status,
            "status": "ok",
            "parsed_candidates": llm_candidates,
            "primary_root_cause": llm_candidates[0]["service"] if llm_candidates else "",
            "evidence_count": len(evidence_summary),
            "llm_input_summary": llm_input_summary,
        }

    def _call_llm(self, case_id, detail, evidence, deps, opsaug_hints, services, guidance=None, llm_input_summary=None, rca_agent_prompt_guidance=None) -> List[Dict]:
        """Call the actual LLM for RCA."""
        guidance = guidance or {}
        prompt_rules = guidance.get("prompt_rules", [])
        matched_skills = guidance.get("matched_skills", [])
        failure_lessons = guidance.get("failure_lessons", [])
        rules_text = "\n".join(f"- {r}" for r in prompt_rules[-3:]) or "- 无"
        skills_text = "\n".join(
            f"- {s.get('prompt_hint', '')} 工具链: {' -> '.join(s.get('tool_chain', [])[:6])}"
            for s in matched_skills[:2]
        ) or "- 无"
        lessons_text = "\n".join(f"- {l.get('lesson', '')}" for l in failure_lessons[-2:]) or "- 无"
        rca_agent_prompt_guidance = rca_agent_prompt_guidance or {}
        context_contract = rca_agent_prompt_guidance.get("context_contract", {}) or {}
        memory_capsules = rca_agent_prompt_guidance.get("memory_capsules", {}) or {}
        active_template = rca_agent_prompt_guidance.get("active_template", {}) or {}
        agent_rules = []
        for key in ("base_rules", "learned_patches", "failure_contrast_rules"):
            vals = rca_agent_prompt_guidance.get(key, [])
            agent_rules.extend(str(v) for v in vals[-4:])
        agent_rules_text = "\n".join(f"- {r}" for r in agent_rules[-10:]) or "- 无"
        context_patch_text = "\n".join(
            f"- {p}" for p in rca_agent_prompt_guidance.get("learned_context_patches", [])[-6:]
        ) or "- 无"
        memory_text = "\n".join(
            f"- {m.get('trigger', '')}: {m.get('learned_policy', '')}"
            for m in rca_agent_prompt_guidance.get("long_term_memory", [])[-4:]
            if isinstance(m, dict)
        ) or "- 无"
        failure_memory_text = "\n".join(
            f"- {m.get('lesson', m)}"
            for m in (memory_capsules.get("failures") or [])[-3:]
            if isinstance(m, dict)
        ) or "- 无"
        semantic_memory_text = "\n".join(
            f"- {m.get('trigger', '')}: {m.get('learned_policy', '')}"
            for m in (memory_capsules.get("semantic") or [])[-3:]
            if isinstance(m, dict)
        ) or "- 无"
        contract_text = json.dumps(context_contract, ensure_ascii=False, default=str)[:1200]
        template_text = json.dumps(active_template, ensure_ascii=False, default=str)[:800]
        raw_text = json.dumps(llm_input_summary or {}, ensure_ascii=False, default=str)[:1800]
        local_llm = self.llm_config and (
            "127.0.0.1" in str(self.llm_config.base_url)
            or "localhost" in str(self.llm_config.base_url).lower()
        )
        if local_llm:
            prompt = (
                "你是云原生 RCA 模型。只输出 JSON，不要解释。\n"
                f"case={case_id}; source={self.data_source.name}\n"
                f"services={', '.join(services[:10])}\n"
                "deps=" + "; ".join(deps[:5]) + "\n"
                "evidence=" + " | ".join((e[:160] for e in evidence[:5])) + "\n"
                "opsaug=" + " | ".join((h[:120] for h in opsaug_hints[:3])) + "\n"
                "rule=根因必须来自 services，区分根因与受害服务。\n"
                'format={"candidates":[{"rank":1,"service":"服务名","score":0.9,"reason":"证据"}]}\n/no_think'
            )
            token_budget = 192
        else:
            prompt = (
                f"你是云原生智能运维助手。请对以下故障案例进行根因分析。\n\n"
                f"## 案例信息\n- 平台: {self.data_source.name}\n- 案例ID: {case_id}\n"
                f"- 服务列表: {', '.join(services[:12])}\n"
                f"- 服务依赖:\n  " + "\n  ".join(deps[:8]) + "\n\n"
                f"## 工具分析证据\n" + "\n".join(f"- {e}" for e in evidence[:8]) + "\n\n"
                f"## OpsAug 多模态提示\n" + "\n".join(f"- {h}" for h in opsaug_hints[:5]) + "\n\n"
                f"## 给到模型的原始故障数据摘要\n{raw_text}\n\n"
                f"## Agent 自进化记忆\n"
                f"### 提示词补丁\n{rules_text}\n"
                f"### 可复用技能\n{skills_text}\n"
                f"### 失败教训\n{lessons_text}\n\n"
                f"## LangChain Multi-Agent RCA System\n"
                f"### 上下文契约\n{contract_text}\n"
                f"### Prompt 模板\n{template_text}\n"
                f"### 上下文学习补丁\n{context_patch_text}\n"
                f"### 多 Agent Prompt 规则\n{agent_rules_text}\n"
                f"### 语义记忆\n{semantic_memory_text}\n"
                f"### 失败轨迹记忆\n{failure_memory_text}\n"
                f"### 长期记忆\n{memory_text}\n\n"
                f"## 任务\n"
                f"仔细分析证据，考虑服务之间的依赖关系和故障传播路径。输出JSON格式的根因候选：\n"
                f'{{"candidates": [{{"rank": 1, "service": "服务名", "score": 0.95, "reason": "理由（基于证据）"}}]}}\n'
                f"只输出JSON，不要其他内容，不要输出<think>。/no_think"
            )
            token_budget = 256
        try:
            llm = self.llm_client(self.llm_config)
            response = llm.chat([
                {"role": "system", "content": "你是云原生运维专家，擅长根因分析。只输出JSON。"},
                {"role": "user", "content": prompt},
            ], max_tokens=token_budget)
            self._last_llm_raw_preview = response[:500]
            start, end = response.find("{"), response.rfind("}")
            if start >= 0 and end > start:
                data = json.loads(response[start:end+1])
                candidates = data.get("candidates", [])
                if candidates:
                    return candidates
            parsed = self._parse_llm_text_candidates(response, services)
            if not parsed:
                self._last_llm_error = "模型返回了文本，但没有可解析的服务候选。"
            return parsed
        except Exception as e:
            self._last_llm_error = str(e)
            return None

    def _parse_llm_text_candidates(self, text: str, services: List[str]) -> Optional[List[Dict[str, Any]]]:
        """Fallback parser for small local models that answer prose instead of JSON."""
        if not text:
            return None
        lower = text.lower()
        hits = []
        for svc in services:
            count = lower.count(str(svc).lower())
            if count:
                hits.append((svc, count))
        if not hits:
            return None
        hits.sort(key=lambda item: item[1], reverse=True)
        return [
            {
                "rank": idx + 1,
                "service": svc,
                "score": round(max(0.2, 0.9 - idx * 0.08), 3),
                "reason": f"本地大模型文本推理中提到 {svc} {count} 次；已从非 JSON 响应中抽取候选。",
                "source": "llm_text_parse",
            }
            for idx, (svc, count) in enumerate(hits[:10])
        ]

    def _builtin_causal_rca(self, detail, tool_evidence, evidence_summary, dep_summary, services, edges) -> List[Dict]:
        """Built-in causal RCA that uses dependency graphs and tool evidence.
        
        This is NOT simple counting - it considers:
        1. Dependency topology (which service calls which)
        2. Causal propagation (errors in downstream may cause errors upstream)
        3. Evidence strength from multiple tools
        4. The actual root cause might be a downstream service even if upstream shows more errors
        """
        # Build dependency maps
        callers = defaultdict(set)  # who calls this service
        callees = defaultdict(set)  # who this service calls
        for e in edges:
            src, tgt = e.get("source", ""), e.get("target", "")
            if src and tgt:
                callers[tgt].add(src)
                callees[src].add(tgt)

        # Extract evidence per service from tools
        service_scores = defaultdict(lambda: {"errors": 0, "anomalies": 0, "tool_weight": 0.0, "reasons": []})

        for te in tool_evidence:
            if not te:
                continue
            tool_name = te.get("tool", "unknown")
            weight = {"DrainMCP": 1.0, "KPIFailure": 1.0, "OpsAug": 0.5, "OpsKB": 0.3, "DynamicEvolutionarySystem": 0.4}.get(tool_name, 0.3)

            # From fault_localizations
            for loc in te.get("fault_localizations", [])[:5]:
                svc = loc.get("service", "")
                if svc and svc in services:
                    service_scores[svc]["tool_weight"] += loc.get("confidence", 0.5) * weight
                    service_scores[svc]["anomalies"] += loc.get("error_count", loc.get("total_anomalies", 1))
                    service_scores[svc]["reasons"].append(loc.get("reason", ""))

            # From root_cause_diagnosis
            diag = te.get("root_cause_diagnosis")
            if isinstance(diag, dict):
                suspect = diag.get("primary_suspect", "")
                if suspect and suspect in services:
                    service_scores[suspect]["tool_weight"] += diag.get("confidence", 0.5) * weight * 0.5

            # From fault_warnings
            for w in te.get("fault_warnings", [])[:10]:
                svc = w.get("service", "")
                if svc and svc in services:
                    service_scores[svc]["errors"] += w.get("count", 1)
                    wt = 1.5 if w.get("severity") == "critical" else 0.8
                    service_scores[svc]["tool_weight"] += wt * weight * 0.3

        # CAUSAL ANALYSIS: errors in downstream services can propagate upstream
        # If service A calls B, and B has errors, A may show errors due to B's failure
        # The root cause is more likely B (the downstream service)
        final_scores = {}
        for svc in services:
            ss = service_scores[svc]
            base = ss["tool_weight"] + ss["anomalies"] * 0.1 + ss["errors"] * 0.05

            # Boost score if this service has direct callers that also show anomalies
            # (propagation pattern: downstream failure causes upstream symptoms)
            downstream_boost = 0.0
            if svc in callers:
                downstream_count = len(callers[svc])
                if downstream_count > 0:
                    # More callers = more potential propagation impact
                    downstream_boost = downstream_count * 0.15
                    # Check if callers also have anomalies (propagation evidence)
                    caller_anomaly_count = sum(1 for c in callers[svc] if service_scores.get(c, {}).get("tool_weight", 0) > 0.1)
                    downstream_boost += caller_anomaly_count * 0.2

            # Penalty: if a service has many callees and THEY have errors,
            # it might just be a victim of downstream failure
            victim_penalty = 0.0
            if svc in callees:
                callee_error_count = sum(service_scores.get(c, {}).get("tool_weight", 0) for c in callees[svc])
                if callee_error_count > base:
                    # This service's errors might be due to downstream failures
                    victim_penalty = base * 0.3

            score = max(0.05, min(0.95, (base + downstream_boost - victim_penalty) / max(base + downstream_boost + 1, 1) * 0.9))
            final_scores[svc] = score

        # Build ranked candidates
        ranked = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
        candidates = []
        for i, (svc, score) in enumerate(ranked[:10]):
            reasons = service_scores[svc]["reasons"][:2]
            reason_text = reasons[0][:120] if reasons else "多工具证据综合指向"
            candidates.append({
                "rank": i + 1,
                "service": svc,
                "score": round(score, 3),
                "reason": reason_text,
                "source": "builtin_causal",
            })
        return candidates

    def _evaluate_acc_at_k(self, rca_result: Dict, ground_truth: str, detail: Dict) -> Dict[str, Any]:
        """HONEST evaluation: check if ground truth service is in top-k candidates."""
        candidates = rca_result.get("parsed_candidates") or rca_result.get("candidates", [])
        if not candidates:
            return {"ACC@1": 0, "ACC@3": 0, "ACC@5": 0, "ACC@10": 0, "MRR": 0, "method": "no_candidates"}

        gt_service = self._extract_service(ground_truth or detail.get("root_cause_ground_truth", ""))
        cand_services = [c["service"].lower().strip() for c in candidates]
        gt_lower = gt_service.lower().strip()

        def hit(k): return 1 if gt_lower in cand_services[:k] else 0
        try:
            idx = cand_services.index(gt_lower)
            mrr = round(1.0 / (idx + 1), 4)
        except ValueError:
            mrr = 0.0

        return {
            "ACC@1": hit(1), "ACC@3": hit(3), "ACC@5": hit(5), "ACC@10": hit(10),
            "MRR": mrr, "ground_truth_service": gt_service,
            "top_candidate": candidates[0]["service"] if candidates else "",
            "candidate_count": len(candidates), "hit_at_1": bool(hit(1)),
            "method": rca_result.get("model", "unknown"),
        }

    @staticmethod
    def _extract_service(text: str) -> str:
        if not text:
            return "unknown"
        m = re.search(r"(\S+)\s+is the root cause", text, re.IGNORECASE)
        if m:
            return m.group(1).strip("().,:;\"'")
        return text.split()[0] if text.split() else "unknown"

    def get_tool_list(self) -> List[Dict[str, str]]:
        return [
            {"name": n, "description": d} for n, d in [
                ("OpsAug", "五种运维模态融合"), ("DrainMCP", "日志单模态分析"),
                ("KPIFailure", "指标单模态分析"), ("DynamicEvolutionarySystem", "系统动态演化"),
                ("OpsKB", "运维知识库"), ("PromCopilot", "PromQL查询生成"),
            ]
        ]
