# -*- coding: utf-8 -*-
"""AIOps RCA adaptation of SkillClaw + Hermes Agent ideas.

This is intentionally not a generic wrapper around either project. SkillClaw
contributes the skill-evolution loop; Hermes contributes context, memory,
trajectory and tool-use patterns. Ops Factory narrows both into an RCA harness
that chooses tools, records failures, and evolves prompt/context skills for
future fault-injection cases.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"
SKILLCLAW_REPO = VENDOR / "SkillClaw"
HERMES_REPO = VENDOR / "hermes-agent"


class SkillHermesAIOpsHarness:
    """Scenario-specific adapter for RCA skill evolution and agent memory."""

    SKILL_BLUEPRINTS = [
        {
            "id": "aiops-context-contract",
            "name": "AIOps 上下文契约技能",
            "trigger": "每次故障注入或企业故障样本进入 RCA 前",
            "source": "Hermes context engine + SkillClaw skill format",
            "goal": "把角色、目标、成功标准、模态证据和停止条件压缩为可复用上下文契约。",
        },
        {
            "id": "fault-propagation-reading",
            "name": "故障传播判读技能",
            "trigger": "拓扑存在调用链、数据库、队列、运行时或业务域节点时",
            "source": "AIOps scenario skill",
            "goal": "区分根因节点、传播节点和受害节点，避免按异常强度直接排序。",
        },
        {
            "id": "adaptive-tool-routing",
            "name": "自适应工具路由技能",
            "trigger": "工具池包含日志、指标、拓扑、知识库或企业工具时",
            "source": "SkillClaw skill retrieval + tool reward",
            "goal": "根据数据模态、历史 reward 和上下文预算选择工具，而不是全部调用。",
        },
        {
            "id": "failure-trajectory-learning",
            "name": "失败轨迹学习技能",
            "trigger": "ACC@1 未命中、LLM 失败、工具报错或 fallback 触发时",
            "source": "Hermes trajectory + SkillClaw session judge",
            "goal": "把错误 Top1、缺失证据、工具选择失误转为下一轮 prompt/context/tool 补丁。",
        },
        {
            "id": "dataset-formatting",
            "name": "训练样本整理技能",
            "trigger": "故障数据收集窗口生成 SFT/RL 数据时",
            "source": "Hermes trajectory export + Ops Factory RCA schema",
            "goal": "把故障注入、工具轨迹、诊断结果整理为 Alpaca SFT 或偏好/RL 样本。",
        },
    ]

    def repo_status(self) -> Dict[str, Any]:
        """Return local checkout status and the concepts we actually use."""

        return {
            "skillclaw": {
                "path": str(SKILLCLAW_REPO),
                "present": SKILLCLAW_REPO.exists(),
                "used_concepts": [
                    "SKILL.md skill registry",
                    "session-level judge",
                    "summarize -> aggregate -> execute evolution loop",
                    "deduplicate and verify learned skills",
                ],
            },
            "hermes_agent": {
                "path": str(HERMES_REPO),
                "present": HERMES_REPO.exists(),
                "used_concepts": [
                    "context engine lifecycle",
                    "memory manager with fenced recalled context",
                    "trajectory saving for successes/failures",
                    "skill preprocessing and toolset gating",
                ],
            },
        }

    def build_agent_adaptation(
        self,
        *,
        detail: Dict[str, Any],
        context: Dict[str, Any],
        tool_plan: List[Dict[str, Any]],
        state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build an RCA-specific harness plan for the current case."""

        selected = [item.get("tool") for item in tool_plan if item.get("selected")]
        skipped = [item.get("tool") for item in tool_plan if not item.get("selected")]
        modalities = context.get("context_layers", {}).get("modalities", {})
        repo = self.repo_status()
        failure_rules = state.get("prompt_engine", {}).get("failure_contrast_rules", [])[-5:]
        prompt_patches = state.get("prompt_engine", {}).get("learned_patches", [])[-5:]
        memory_capsules = context.get("memory_capsules") or {}
        skill_matches = self._select_skills(modalities, selected, detail)

        return {
            "harness_id": f"skill-hermes-aiops-{uuid.uuid4().hex[:8]}",
            "adaptation_mode": "skillclaw_skill_evolution_plus_hermes_memory_context",
            "repos": repo,
            "case_binding": {
                "case_id": detail.get("case_id"),
                "case_name": detail.get("case_name"),
                "source": detail.get("source"),
                "fault_injection": detail.get("fault_injection", {}),
            },
            "skillclaw_layer": {
                "library": "AIOps RCA SKILL.md blueprints",
                "matched_skills": skill_matches,
                "evolution_loop": [
                    "capture_rca_session",
                    "judge_success_or_failure",
                    "summarize_failure_or_success_pattern",
                    "merge_deduplicate_skill_patch",
                    "verify_on_next_fault_case",
                ],
            },
            "hermes_layer": {
                "context_engine": {
                    "budget": context.get("context_contract", {}).get("budget", {}),
                    "protected_context": ["fault injection contract", "topology propagation", "top evidence", "ground truth only for evaluator"],
                    "compression_policy": "preserve modality summaries and artifact diffs; drop repeated raw rows",
                },
                "memory_manager": {
                    "short_term": memory_capsules.get("short_term", {}),
                    "semantic_hits": len(memory_capsules.get("semantic") or []),
                    "failure_hits": len(memory_capsules.get("failures") or []),
                    "fencing": "recalled memory is background context, never treated as user input or ground truth",
                },
                "trajectory": {
                    "format": "fault_case -> agent_handoffs -> tool_calls -> llm_rca -> evaluator -> skill_update",
                    "failure_capture": ["wrong_top1", "missing_modality", "tool_error", "fallback_reason"],
                },
            },
            "opsfactory_binding": {
                "selected_tools": selected,
                "skipped_tools": skipped,
                "prompt_updates_ready": prompt_patches,
                "failure_rules_ready": failure_rules,
                "enterprise_tool_hook": "registered enterprise tools enter the same adaptive router and reward ledger",
            },
            "runtime_gates": [
                {"gate": "context_contract_ready", "owner": "context_prompt_agent"},
                {"gate": "skill_retrieval_ready", "owner": "skill_harness_agent"},
                {"gate": "tool_plan_confirmed", "owner": "tool_decision_agent"},
                {"gate": "rca_evaluated", "owner": "critic_learning_agent"},
            ],
        }

    def build_collection_policy(self, platforms: List[str], format_type: str) -> Dict[str, Any]:
        return {
            "collector": "SkillClaw-Hermes AIOps dataset harness",
            "platforms": platforms,
            "format_type": format_type,
            "skill": "dataset-formatting",
            "trajectory_contract": [
                "fault_injection_window",
                "raw_log_trace_metric_alert_samples",
                "selected_tool_plan",
                "agent_handoff_trace",
                "final_rca_or_ground_truth",
                "judge_reward_or_sft_answer",
            ],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _select_skills(
        self,
        modalities: Dict[str, Any],
        selected_tools: List[str],
        detail: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        matched: List[Dict[str, Any]] = []
        modality_counts = {
            key: value.get("count", value.get("summary_count", 0)) if isinstance(value, dict) else 0
            for key, value in modalities.items()
        }
        for skill in self.SKILL_BLUEPRINTS:
            include = skill["id"] in {"aiops-context-contract", "failure-trajectory-learning"}
            if skill["id"] == "fault-propagation-reading":
                include = bool((detail.get("service_graph") or {}).get("edges"))
            elif skill["id"] == "adaptive-tool-routing":
                include = bool(selected_tools)
            elif skill["id"] == "dataset-formatting":
                include = False
            if include:
                item = dict(skill)
                item["activation_reason"] = (
                    f"modalities={modality_counts}, selected_tools={selected_tools or ['none']}"
                )
                matched.append(item)
        return matched
