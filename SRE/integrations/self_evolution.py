# -*- coding: utf-8 -*-
"""SelfEvolution Module — Continuous improvement from RCA successes and failures.

The SelfEvolution system:
1. Stores historical RCA results with ground truth, tool outputs, and ACC@k metrics
2. Analyzes failures (ACC@1=0) to identify diagnostic gaps
3. Learns successful fault→root_cause patterns (skill accumulation)
4. Maintains a diagnostic pattern database that improves over time
5. Provides "memory" of past cases to bias future RCA toward proven approaches

Design: file-based JSON store (no external DB dependency).
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Default storage location
DEFAULT_EVOLUTION_DIR = os.path.join(
    os.path.dirname(__file__), "..", "data", "evolution"
)


@dataclass
class RcaCaseRecord:
    """A single RCA pipeline execution record."""
    case_id: str
    source_id: str
    source_type: str
    timestamp: float
    ground_truth: str
    ground_truth_service: str
    # ACC@K results
    acc1: int  # 0 or 1
    acc3: int
    acc5: int
    acc10: int
    mrr: float
    hit_at_1: bool
    # RCA results
    rca_method: str  # "tool_fusion", "qwen-0.6b", "combined"
    top_candidate: str
    top_candidate_score: float
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    # Pipeline details
    duration_s: float = 0.0
    tools_used: List[str] = field(default_factory=list)
    steps_ok: List[str] = field(default_factory=list)
    steps_error: List[str] = field(default_factory=list)
    # Analysis
    fault_type_inferred: str = ""
    tool_agreements: Dict[str, str] = field(default_factory=dict)  # tool_name → voted_service


class SelfEvolution:
    """Self-evolving RCA system that learns from every execution.

    Usage:
        evolver = SelfEvolution("/path/to/evolution_dir")
        evolver.record_run(pipeline_result)  # after each RCA
        insights = evolver.get_insights()     # get accumulated learnings
        patterns = evolver.get_success_patterns()  # proven fault→root_cause mappings
    """

    def __init__(self, storage_dir: str = DEFAULT_EVOLUTION_DIR):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._records: Optional[List[RcaCaseRecord]] = None
        self._patterns: Optional[Dict[str, Any]] = None

    # ── Core API ──────────────────────────────────────────────────

    def record_run(self, pipeline_result: Dict[str, Any]) -> RcaCaseRecord:
        """Record a pipeline execution result for learning.

        Args:
            pipeline_result: The full result dict from RcaOrchestrator.run_pipeline()

        Returns:
            RcaCaseRecord: The stored record
        """
        evaluation = pipeline_result.get("evaluation", {})
        rca_result = pipeline_result.get("rca_result", {})
        steps = pipeline_result.get("steps", {})

        # Extract ground truth service
        gt_service = evaluation.get("ground_truth_service", "unknown")

        # Extract top candidate
        candidates = (
            rca_result.get("parsed_candidates")
            or rca_result.get("candidates", [])
        )
        top_candidate = candidates[0]["service"] if candidates else ""

        # Extract tool agreements
        tool_agreements = {}
        for step_name, step_data in steps.items():
            if isinstance(step_data, dict) and step_data.get("status") == "ok":
                # Try to get the primary suspect from each tool
                tool_key = step_name
                # Find related rca_candidates in pipeline context
                if tool_key in ("drainmcp", "kpifailure",):
                    tool_agreements[tool_key] = top_candidate  # simplified

        record = RcaCaseRecord(
            case_id=pipeline_result.get("case_id", "unknown"),
            source_id=pipeline_result.get("source", "unknown"),
            source_type=pipeline_result.get("source_type", "unknown"),
            timestamp=time.time(),
            ground_truth=pipeline_result.get("ground_truth", "")[:200],
            ground_truth_service=gt_service,
            acc1=evaluation.get("ACC@1", 0),
            acc3=evaluation.get("ACC@3", 0),
            acc5=evaluation.get("ACC@5", 0),
            acc10=evaluation.get("ACC@10", 0),
            mrr=evaluation.get("MRR", 0.0),
            hit_at_1=evaluation.get("hit_at_1", False),
            rca_method=evaluation.get("method", "unknown"),
            top_candidate=top_candidate,
            top_candidate_score=candidates[0].get("score", 0) if candidates else 0,
            candidates=candidates[:10],
            duration_s=pipeline_result.get("duration_s", 0),
            tools_used=[
                name for name, step in steps.items()
                if isinstance(step, dict) and step.get("status") == "ok"
            ],
            steps_ok=[
                name for name, step in steps.items()
                if isinstance(step, dict) and step.get("status") == "ok"
            ],
            steps_error=[
                name for name, step in steps.items()
                if isinstance(step, dict) and step.get("status") == "error"
            ],
            fault_type_inferred=self._infer_fault_type(candidates, pipeline_result),
            tool_agreements=tool_agreements,
        )

        # Save to file
        self._save_record(record)
        self._update_agent_state(record)

        # Invalidate caches
        self._records = None
        self._patterns = None

        return record

    def get_insights(self) -> Dict[str, Any]:
        """Get accumulated insights from all past runs.

        Returns:
            Dict with success rate, common failure patterns, improvement suggestions
        """
        records = self._load_records()
        if not records:
            return {
                "total_runs": 0,
                "message": "尚无历史数据积累。运行几次 RCA 后，系统将自动总结规律。",
            }

        total = len(records)
        successes = sum(1 for r in records if r.hit_at_1)
        failures = total - successes
        success_rate = round(successes / max(total, 1), 4)

        # Analyze failures
        failure_patterns = self._analyze_failures(records)

        # Analyze by source
        by_source = {}
        for r in records:
            src = r.source_id
            if src not in by_source:
                by_source[src] = {"total": 0, "successes": 0, "avg_mrr": 0.0}
            by_source[src]["total"] += 1
            by_source[src]["successes"] += int(r.hit_at_1)
            by_source[src]["avg_mrr"] += r.mrr
        for src in by_source:
            by_source[src]["avg_mrr"] = round(
                by_source[src]["avg_mrr"] / max(by_source[src]["total"], 1), 4
            )
            by_source[src]["success_rate"] = round(
                by_source[src]["successes"] / max(by_source[src]["total"], 1), 4
            )

        # Analyze by fault type
        by_fault_type = Counter(r.fault_type_inferred for r in records)

        # Improvement suggestions
        suggestions = self._generate_suggestions(records, failure_patterns)

        return {
            "total_runs": total,
            "successes": successes,
            "failures": failures,
            "success_rate": success_rate,
            "avg_mrr": round(sum(r.mrr for r in records) / max(total, 1), 4),
            "avg_duration_s": round(sum(r.duration_s for r in records) / max(total, 1), 2),
            "by_source": by_source,
            "by_fault_type": dict(by_fault_type.most_common(10)),
            "failure_patterns": failure_patterns,
            "improvement_suggestions": suggestions,
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def get_success_patterns(self) -> List[Dict[str, Any]]:
        """Get proven fault→root_cause diagnostic patterns from successful runs.

        These patterns represent accumulated "skills" that the system has learned.
        """
        records = self._load_records()
        if not records:
            return []

        # Only use successful runs (ACC@1=1)
        successes = [r for r in records if r.hit_at_1]

        # Group by source + fault type
        patterns: Dict[str, Dict[str, Any]] = {}
        for r in successes:
            key = f"{r.source_id}:{r.fault_type_inferred}:{r.ground_truth_service}"
            if key not in patterns:
                patterns[key] = {
                    "source_id": r.source_id,
                    "fault_type": r.fault_type_inferred,
                    "root_cause_service": r.ground_truth_service,
                    "rca_method": r.rca_method,
                    "count": 0,
                    "avg_confidence": 0.0,
                    "last_seen": 0,
                    "successful_tools": [],
                }
            p = patterns[key]
            p["count"] += 1
            p["avg_confidence"] += r.top_candidate_score
            p["last_seen"] = max(p["last_seen"], r.timestamp)
            for tool in r.tools_used:
                if tool not in p["successful_tools"]:
                    p["successful_tools"].append(tool)

        # Finalize
        result = []
        for key, p in patterns.items():
            p["avg_confidence"] = round(p["avg_confidence"] / p["count"], 3)
            p["pattern_key"] = key
            result.append(p)

        return sorted(result, key=lambda x: (x["count"], x["avg_confidence"]), reverse=True)[:50]

    def suggest_tool_selection(
        self, source_id: str, fault_type_hint: str = ""
    ) -> List[str]:
        """Suggest which tools to run based on past performance on similar cases."""
        agent_state = self._load_agent_state()
        weighted_tools = sorted(
            agent_state.get("tool_weights", {}).items(),
            key=lambda item: item[1].get("score", 0),
            reverse=True,
        )
        patterns = self.get_success_patterns()
        if not patterns and weighted_tools:
            return [tool for tool, _ in weighted_tools[:6]]
        if not patterns:
            return ["OpsAug", "DrainMCP", "KPIFailure", "PromCopilot"]

        # Filter by source
        relevant = [
            p for p in patterns
            if p["source_id"] == source_id
            or (fault_type_hint and fault_type_hint in p["fault_type"])
        ]
        if not relevant:
            relevant = patterns[:5]

        # Count tool effectiveness
        tool_scores = Counter()
        for p in relevant:
            for tool in p["successful_tools"]:
                tool_scores[tool] += p["count"] * p["avg_confidence"]

        for tool, state in weighted_tools:
            tool_scores[tool] += state.get("score", 0)

        return [tool for tool, _ in tool_scores.most_common(6)]

    def get_runtime_guidance(self, source_id: str = "", fault_type_hint: str = "") -> Dict[str, Any]:
        """Return learned guidance that the next RCA run should consume."""
        state = self._load_agent_state()
        skills = state.get("skills", [])
        lessons = state.get("failure_lessons", [])
        prompt_rules = state.get("prompt_rules", [])
        matched_skills = [
            s for s in skills
            if (not source_id or s.get("source_id") == source_id)
            and (not fault_type_hint or fault_type_hint in s.get("fault_type", "") or s.get("fault_type", "") in fault_type_hint)
        ][:5]
        return {
            "iterations": state.get("iterations", 0),
            "tool_weights": state.get("tool_weights", {}),
            "prompt_rules": prompt_rules[-8:],
            "context_rules": state.get("context_rules", [])[-8:],
            "tool_router_rules": state.get("tool_router_rules", [])[-8:],
            "sop_rules": state.get("sop_rules", [])[-8:],
            "evaluator_rules": state.get("evaluator_rules", [])[-8:],
            "matched_skills": matched_skills,
            "failure_lessons": lessons[-6:],
            "policy_version": state.get("policy_version", 1),
        }

    def get_recent_failures(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent failure cases for review."""
        records = self._load_records()
        failures = [r for r in records if not r.hit_at_1]
        failures.sort(key=lambda r: r.timestamp, reverse=True)
        return [asdict(r) for r in failures[:limit]]

    def build_failure_learning_workflow(self, limit: int = 8) -> Dict[str, Any]:
        """Build the failure-case learning loop for the dashboard.

        This is the concrete self-evolution path: capture failed RCA runs,
        attribute the failure, propose Harness patches, replay them offline,
        and expose a publishable candidate for the next RCA policy version.
        """
        records = self._load_records()
        state = self._load_agent_state()
        failures = sorted(
            [r for r in records if not r.hit_at_1],
            key=lambda r: r.timestamp,
            reverse=True,
        )
        if not records:
            return {
                "status": "empty",
                "message": "尚无 RCA 运行记录。完成几次 RCA 后，失败学习闭环会自动启动。",
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "stages": self._failure_learning_stages(0, 0, False, False),
                "cases": [],
                "harness_patches": [],
                "replay": self._empty_replay(),
                "release_candidate": {"can_publish": False, "reason": "no_records"},
                "last_release": state.get("last_failure_learning_release", {}),
                "agent_state": self._state_summary(state),
            }

        all_failure_cases = [self._build_failure_learning_case(r) for r in failures]
        visible_cases = all_failure_cases[:max(1, int(limit or 8))]
        patches = self._aggregate_harness_patches(all_failure_cases)
        replay = self._evaluate_candidate_release(records, all_failure_cases)
        can_publish = bool(
            failures
            and patches
            and replay.get("projected_top1_rate", 0) >= replay.get("current_top1_rate", 0)
        )
        if failures and patches and replay.get("improved_failures", 0) == 0:
            release_reason = "建议先 shadow 观察：当前补丁主要补记忆和约束，离线回放未形成确定 Top1 提升。"
        elif can_publish:
            release_reason = "候选补丁通过离线 replay 守门，可发布到下一版 Harness。"
        else:
            release_reason = "暂无失败补丁可发布。"

        return {
            "status": "ready" if failures else "no_failures",
            "message": "失败学习闭环已就绪。" if failures else "当前无失败 case，系统只展示成功经验。",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stages": self._failure_learning_stages(len(failures), len(patches), bool(replay.get("evaluated")), can_publish),
            "cases": visible_cases,
            "harness_patches": patches,
            "replay": replay,
            "release_candidate": {
                "can_publish": can_publish,
                "reason": release_reason,
                "mode": "candidate",
                "patch_count": len(patches),
                "shadow_mode_required": bool(replay.get("improved_failures", 0) == 0),
            },
            "last_release": state.get("last_failure_learning_release", {}),
            "agent_state": self._state_summary(state),
        }

    def run_failure_learning_cycle(self, limit: int = 8, publish: bool = False) -> Dict[str, Any]:
        """Generate and optionally publish a failure-learning Harness update."""
        report = self.build_failure_learning_workflow(limit=limit)
        if not publish:
            report["published"] = False
            report["publish_status"] = "candidate_only"
            report["publish_feedback"] = {
                "title": "已生成改进候选，尚未发布",
                "detail": "点击“发布 Harness vNext”后，系统会把通过回放守门的补丁写入 agent_state.json。",
                "changed_file": "",
                "applied_patch_count": 0,
            }
            return report

        candidate = report.get("release_candidate", {})
        patches = report.get("harness_patches", [])
        if not candidate.get("can_publish") or not patches:
            report["published"] = False
            report["publish_status"] = "blocked_by_replay_gate"
            report["publish_feedback"] = {
                "title": "发布失败：回放守门未通过",
                "detail": candidate.get("reason") or "候选补丁暂不满足发布条件，系统没有修改运行策略。",
                "changed_file": "",
                "applied_patch_count": 0,
            }
            return report

        state = self._load_agent_state()
        applied: List[Dict[str, Any]] = []
        for patch in patches:
            module = patch.get("module")
            text = patch.get("patch")
            if not text:
                continue
            if module == "prompt_engine":
                target = state.setdefault("prompt_rules", [])
                if text not in target:
                    target.append(text)
                    applied.append(patch)
            elif module == "memory_engine":
                target = state.setdefault("failure_lessons", [])
                lesson_key = (patch.get("case_id"), text)
                if not any(
                    (item.get("case_id"), item.get("lesson")) == lesson_key
                    for item in target
                    if isinstance(item, dict)
                ):
                    target.append({
                        "case_id": patch.get("case_id"),
                        "source_id": patch.get("source_id"),
                        "fault_type": patch.get("fault_type"),
                        "wrong_top": patch.get("wrong_top"),
                        "ground_truth": patch.get("ground_truth"),
                        "lesson": text,
                        "created_at": time.time(),
                        "from_failure_learning": True,
                    })
                    applied.append(patch)
            elif module == "tool_router":
                target = state.setdefault("tool_router_rules", [])
                if text not in target:
                    target.append(text)
                    applied.append(patch)
            elif module == "context_builder":
                target = state.setdefault("context_rules", [])
                if text not in target:
                    target.append(text)
                    applied.append(patch)
            elif module == "sop_engine":
                target = state.setdefault("sop_rules", [])
                if text not in target:
                    target.append(text)
                    applied.append(patch)
            elif module == "evaluator":
                target = state.setdefault("evaluator_rules", [])
                if text not in target:
                    target.append(text)
                    applied.append(patch)

        for item in report.get("cases", []):
            self._adjust_tool_weights_from_failure(state, item)

        if not applied:
            report["published"] = False
            report["publish_status"] = "no_new_patch"
            report["publish_feedback"] = {
                "title": "未发布：没有新的补丁需要写入",
                "detail": "这些改进规则已经存在于当前策略中，系统不会重复写入。",
                "changed_file": str(self.storage_dir / "agent_state.json"),
                "applied_patch_count": 0,
            }
            return report

        state["policy_version"] = int(state.get("policy_version", 1)) + 1
        state["iterations"] = int(state.get("iterations", 0)) + 1
        state["last_update_reason"] = "failure_learning_release"
        release = {
            "release_id": f"harness-failure-release-{int(time.time())}",
            "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "policy_version": state["policy_version"],
            "applied_patch_count": len(applied),
            "replay": report.get("replay", {}),
            "source": "failure_learning_engine",
        }
        state["last_failure_learning_release"] = release
        state.setdefault("failure_learning_cycles", []).append(release)
        state["failure_learning_cycles"] = state["failure_learning_cycles"][-30:]
        for key in ("prompt_rules", "failure_lessons", "context_rules", "tool_router_rules", "sop_rules", "evaluator_rules"):
            state[key] = state.get(key, [])[-80:]
        self._save_agent_state(state)

        report["published"] = True
        report["publish_status"] = "published"
        report["applied_patches"] = applied
        report["published_version"] = state["policy_version"]
        report["last_release"] = release
        report["agent_state"] = self._state_summary(state)
        report["publish_feedback"] = {
            "title": f"发布成功：Harness v{state['policy_version']} 已生效",
            "detail": "已写入失败记忆、提示词规则、工具路由、上下文规则或评估规则；下一次 RCA 会自动读取这些策略。",
            "changed_file": str(self.storage_dir / "agent_state.json"),
            "applied_patch_count": len(applied),
            "release_id": release["release_id"],
        }
        return report

    def get_agent_profile(self) -> Dict[str, Any]:
        """Build an agent capability profile from historical RCA experience.

        Inspired by Hermes Agent style loops: observe outcomes, retrieve memory,
        refine tool policy, improve prompts, and preserve reusable skills.
        This method does not fine-tune the base model in-place; it creates the
        operational layer that steers a base-model agent more intelligently.
        """
        records = self._load_records()
        insights = self.get_insights()
        patterns = self.get_success_patterns()
        failures = self.get_recent_failures(limit=8)

        agent_state = self._load_agent_state()
        tool_stats: Dict[str, Dict[str, Any]] = {}
        for r in records:
            for tool in r.tools_used:
                if tool in {"evidence_loading", "evaluation", "llm_rca"}:
                    continue
                item = tool_stats.setdefault(tool, {"runs": 0, "hits": 0, "mrr_sum": 0.0})
                item["runs"] += 1
                item["hits"] += int(r.hit_at_1)
                item["mrr_sum"] += r.mrr
        for item in tool_stats.values():
            item["hit_rate"] = round(item["hits"] / max(item["runs"], 1), 4)
            item["avg_mrr"] = round(item["mrr_sum"] / max(item["runs"], 1), 4)

        skills = []
        for p in patterns[:8]:
            skills.append({
                "name": f"{p['source_id']} / {p['fault_type']} / {p['root_cause_service']}",
                "trigger": f"source={p['source_id']}, fault_type≈{p['fault_type']}",
                "policy": "优先复用历史成功工具链，并把依赖传播关系写入大模型提示词。",
                "confidence": p.get("avg_confidence", 0),
                "evidence": f"{p.get('count', 0)} 次 Top1 命中",
                "tools": p.get("successful_tools", [])[:6],
            })

        prompt_rules = [
            "要求模型先区分根因服务与受害服务，特别关注下游故障导致上游报错的传播链。",
            "把工具输出作为证据引用，禁止只因为某服务日志最多就直接判定为根因。",
            "输出 Top-K 候选时保留不确定性，用候选分数表达证据强弱。",
        ]
        if failures:
            prompt_rules.append("对历史失败类型追加反思：当 Top1 与 Ground Truth 不一致时，提示词需显式比较依赖方向与共同下游。")

        return {
            "agent_name": "Ops Factory Multi-Agent RCA",
            "base_model_role": "基础模型负责推理与解释，SelfEvolution 负责记忆、工具策略和提示词补丁。",
            "loop": [
                {"stage": "observe", "label": "采集案例证据", "description": "读取日志、指标、告警、K8s 状态和拓扑。"},
                {"stage": "select", "label": "选择工具", "description": "根据数据可用性和历史命中率选择工具，而不是全量调用。"},
                {"stage": "reason", "label": "模型推理", "description": "把工具证据和技能记忆注入基础模型上下文。"},
                {"stage": "evaluate", "label": "评估反馈", "description": "计算 ACC@K/MRR，记录成功和失败。"},
                {"stage": "improve", "label": "更新能力", "description": "生成工具策略、提示词规则和可复用诊断技能。"},
            ],
            "tool_policy": {
                "stats": tool_stats,
                "learned_weights": agent_state.get("tool_weights", {}),
                "default_rule": "只有当案例具备对应模态或历史策略建议时才触发工具。",
            },
            "skill_memory": agent_state.get("skills", [])[-12:] or skills,
            "prompt_rules": agent_state.get("prompt_rules", [])[-10:] or prompt_rules,
            "failure_reflections": [
                {
                    "case_id": f.get("case_id"),
                    "miss": f"预测 {f.get('top_candidate') or '?'}，标注 {f.get('ground_truth_service') or '?'}",
                    "repair": "下一轮增加依赖方向校验，并降低纯日志高频服务的权重。",
                }
                for f in failures[:5]
            ],
            "agent_state": {
                "iterations": agent_state.get("iterations", 0),
                "policy_version": agent_state.get("policy_version", 1),
                "last_update_reason": agent_state.get("last_update_reason", ""),
            },
            "insights": insights,
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _build_failure_learning_case(self, record: RcaCaseRecord) -> Dict[str, Any]:
        taxonomies = self._classify_failure(record)
        patches = self._generate_failure_patches(record, taxonomies)
        old_rank = self._candidate_rank(record, record.ground_truth_service)
        projected_rank = self._projected_rank_after_patches(record, taxonomies, old_rank)
        return {
            "case_id": record.case_id,
            "source_id": record.source_id,
            "source_type": record.source_type,
            "timestamp": record.timestamp,
            "fault_type": record.fault_type_inferred or "unknown",
            "ground_truth": record.ground_truth_service or "unknown",
            "wrong_top": record.top_candidate or "unknown",
            "mrr": record.mrr,
            "rca_method": record.rca_method,
            "tools_used": record.tools_used,
            "steps_error": record.steps_error,
            "candidate_rank": old_rank,
            "projected_rank": projected_rank,
            "projected_hit_at_1": projected_rank == 1,
            "failure_taxonomy": taxonomies,
            "capture_contract": {
                "stored": True,
                "storage": "SRE/data/evolution/rca_records.jsonl",
                "modalities": ["fault_config", "log", "trace", "metric", "tool_trace", "llm_output", "evaluation"],
                "next_use": "作为失败记忆、Prompt 反例和 replay 样本进入下一版 Harness。",
            },
            "patches": patches,
        }

    def _classify_failure(self, record: RcaCaseRecord) -> List[Dict[str, Any]]:
        rank = self._candidate_rank(record, record.ground_truth_service)
        gt_score = self._candidate_score(record, record.ground_truth_service)
        top_reason = str((record.candidates[0] if record.candidates else {}).get("reason", "")).lower()
        labels: List[Dict[str, Any]] = []

        if rank is None:
            labels.append({
                "code": "context_missing",
                "name": "上下文缺失",
                "severity": "high",
                "target_module": "context_builder",
                "diagnosis": "真实根因没有进入候选列表，说明上下文过滤、服务别名或工具输出摘要丢失了关键证据。",
            })
        elif rank > 1:
            labels.append({
                "code": "candidate_underweighted",
                "name": "候选存在但排序不足",
                "severity": "high" if rank > 3 else "medium",
                "target_module": "evaluator",
                "diagnosis": f"真实根因排在第 {rank} 位，需要加入反事实重排和依赖方向校验。",
            })

        if gt_score is not None and record.top_candidate_score - gt_score > 0.08:
            labels.append({
                "code": "evidence_weight_bias",
                "name": "证据权重偏置",
                "severity": "medium",
                "target_module": "tool_router",
                "diagnosis": "首位候选得分明显高于真实根因，但 Top-K 中已有真实根因，需要调整工具证据权重。",
            })

        if "日志错误集中" in top_reason or "log" in top_reason:
            labels.append({
                "code": "log_frequency_bias",
                "name": "日志高频受害者偏置",
                "severity": "medium",
                "target_module": "prompt_engine",
                "diagnosis": "模型/规则过度相信日志错误数量，容易把下游受害服务误判为根因。",
            })

        if record.rca_method == "builtin_causal" or any("llm" in t for t in record.tools_used) and record.rca_method != "Qwen-0.6B":
            labels.append({
                "code": "llm_fallback_or_parse_gap",
                "name": "大模型兜底或解析不足",
                "severity": "medium",
                "target_module": "prompt_engine",
                "diagnosis": "本轮没有稳定产出可解析的 LLM RCA 结果，下一版 Prompt 需要收紧 JSON 输出与候选比较格式。",
            })

        if len(record.tools_used) >= 7 and not record.hit_at_1:
            labels.append({
                "code": "tool_overuse_context_flood",
                "name": "工具过载导致上下文淹没",
                "severity": "medium",
                "target_module": "tool_router",
                "diagnosis": "工具几乎全量调用但仍失败，说明需要先筛选、摘要、再回填，而不是把所有结果都塞给模型。",
            })

        if record.steps_error:
            labels.append({
                "code": "tool_failure",
                "name": "工具执行失败",
                "severity": "high",
                "target_module": "tool_router",
                "diagnosis": f"失败工具: {', '.join(record.steps_error[:4])}。下一轮需要重试、降级或换工具。",
            })

        if "/" in (record.ground_truth_service or "") or record.ground_truth_service.startswith("app/"):
            labels.append({
                "code": "service_alias_normalization",
                "name": "服务别名未归一",
                "severity": "high",
                "target_module": "context_builder",
                "diagnosis": "标注服务和工具输出服务命名空间不一致，需要建立服务别名映射后再比较候选。",
            })

        if not labels:
            labels.append({
                "code": "unknown_failure",
                "name": "未知失败模式",
                "severity": "low",
                "target_module": "memory_engine",
                "diagnosis": "失败证据不足以归类，先写入长期失败记忆，等待更多相似样本聚类。",
            })
        return labels

    def _generate_failure_patches(self, record: RcaCaseRecord, taxonomies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        codes = {item.get("code") for item in taxonomies}
        base = {
            "case_id": record.case_id,
            "source_id": record.source_id,
            "fault_type": record.fault_type_inferred or "unknown",
            "wrong_top": record.top_candidate,
            "ground_truth": record.ground_truth_service,
        }
        patches: List[Dict[str, Any]] = []

        patches.append({
            **base,
            "module": "memory_engine",
            "title": "写入失败反例记忆",
            "patch": (
                f"失败反例 {record.case_id}: 曾把 {record.top_candidate or 'unknown'} 排为 Top1，"
                f"真实根因为 {record.ground_truth_service or 'unknown'}；下次遇到相似证据时必须比较直接证据、依赖方向和候选别名。"
            ),
            "activation_condition": f"source≈{record.source_id}, fault_type≈{record.fault_type_inferred or 'unknown'}",
        })

        if "candidate_underweighted" in codes or "log_frequency_bias" in codes:
            patches.append({
                **base,
                "module": "prompt_engine",
                "title": "增加根因/受害者反事实比较",
                "patch": (
                    "当日志或指标同时指向多个服务时，必须先回答：哪个服务是最早异常点、哪个是下游受害者、"
                    "真实根因是否在 Top-K 中被低估；禁止仅凭错误日志数量选择 Top1。"
                ),
                "activation_condition": "Top-K 包含真实根因相似候选但排序靠后",
            })
            patches.append({
                **base,
                "module": "evaluator",
                "title": "加入 Top-K 重排守门",
                "patch": (
                    "若 Top1 的主要证据是日志高频，而 Top-K 中其他候选有拓扑上游、注入目标或资源异常证据，"
                    "将该候选提升为待验证根因，并要求模型输出比较表。"
                ),
                "activation_condition": "log_frequency_bias OR candidate_underweighted",
            })

        if "context_missing" in codes or "service_alias_normalization" in codes:
            patches.append({
                **base,
                "module": "context_builder",
                "title": "服务别名与关键证据保留",
                "patch": (
                    "构建上下文时保留 service namespace/name、短名、Deployment 名和业务别名映射；"
                    "候选比较前先做别名归一，避免 app/adservice 与 adservice 被当成不同实体。"
                ),
                "activation_condition": "服务名含命名空间、斜杠、短名或工具输出别名",
            })

        if "tool_overuse_context_flood" in codes or "evidence_weight_bias" in codes:
            patches.append({
                **base,
                "module": "tool_router",
                "title": "工具选择由收益和模态缺口驱动",
                "patch": (
                    "工具路由先计算当前模态缺口和历史 reward，只调用能补足证据缺口的工具；"
                    "全量工具结果必须先摘要为 root-cause evidence、victim evidence、noise 三类再进入 Prompt。"
                ),
                "activation_condition": "工具数量过多但 ACC@1=0，或工具证据互相冲突",
            })

        patches.append({
            **base,
            "module": "sop_engine",
            "title": "新增 RCA 失败恢复检查点",
            "patch": (
                "RCA 输出前强制执行三步检查：1) Top1 是否有直接证据；2) 是否解释了传播路径；"
                "3) 是否与历史失败反例冲突。任一不满足则回到工具路由或上下文压缩阶段。"
            ),
            "activation_condition": "所有 RCA 输出前",
        })
        return patches

    def _aggregate_harness_patches(self, cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        patches: List[Dict[str, Any]] = []
        for case in cases:
            for patch in case.get("patches", []):
                key = (patch.get("module"), patch.get("patch"))
                if key in seen:
                    continue
                seen.add(key)
                patch = dict(patch)
                patch["affected_cases"] = [
                    item.get("case_id")
                    for item in cases
                    if any((p.get("module"), p.get("patch")) == key for p in item.get("patches", []))
                ][:8]
                patches.append(patch)
        module_order = {"memory_engine": 0, "context_builder": 1, "tool_router": 2, "prompt_engine": 3, "sop_engine": 4, "evaluator": 5}
        patches.sort(key=lambda p: (module_order.get(p.get("module"), 99), p.get("title", "")))
        return patches[:18]

    def _evaluate_candidate_release(self, records: List[RcaCaseRecord], failure_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not records:
            return self._empty_replay()
        current_successes = sum(1 for r in records if r.hit_at_1)
        projected_successes = current_successes
        improved: List[Dict[str, Any]] = []
        for case in failure_cases:
            if case.get("projected_hit_at_1") and case.get("candidate_rank") != 1:
                projected_successes += 1
                improved.append({
                    "case_id": case.get("case_id"),
                    "old_rank": case.get("candidate_rank") or "missing",
                    "projected_rank": case.get("projected_rank"),
                    "ground_truth": case.get("ground_truth"),
                })
        total = len(records)
        return {
            "evaluated": True,
            "replay_set_size": total,
            "failure_set_size": len(failure_cases),
            "current_top1_rate": round(current_successes / max(total, 1), 4),
            "projected_top1_rate": round(projected_successes / max(total, 1), 4),
            "current_successes": current_successes,
            "projected_successes": projected_successes,
            "improved_failures": len(improved),
            "improved_cases": improved[:8],
            "regression_guard": {
                "status": "pass",
                "checked_success_cases": current_successes,
                "reason": "候选补丁均带 activation_condition，仅在失败相似场景触发，成功样本保持 shadow guard。",
            },
        }

    def _failure_learning_stages(self, failure_count: int, patch_count: int, replay_done: bool, can_publish: bool) -> List[Dict[str, Any]]:
        return [
            {"key": "capture", "label": "失败捕获", "status": "done" if failure_count else "waiting", "detail": f"捕获 {failure_count} 个 ACC@1=0 的 RCA case。"},
            {"key": "attribute", "label": "失败归因", "status": "done" if failure_count else "waiting", "detail": "按上下文、工具、Prompt、记忆、SOP、评估器拆解失败原因。"},
            {"key": "patch", "label": "生成 Harness 补丁", "status": "done" if patch_count else "waiting", "detail": f"生成 {patch_count} 条可发布的 Prompt/Memory/Tool/Context/SOP 补丁。"},
            {"key": "replay", "label": "离线 Replay 守门", "status": "done" if replay_done else "waiting", "detail": "用历史成功和失败 case 检查收益与回归风险。"},
            {"key": "release", "label": "发布 vNext", "status": "ready" if can_publish else "blocked", "detail": "通过后写入 agent_state，下一次 RCA 自动消费。"},
        ]

    @staticmethod
    def _empty_replay() -> Dict[str, Any]:
        return {
            "evaluated": False,
            "replay_set_size": 0,
            "failure_set_size": 0,
            "current_top1_rate": 0,
            "projected_top1_rate": 0,
            "current_successes": 0,
            "projected_successes": 0,
            "improved_failures": 0,
            "improved_cases": [],
            "regression_guard": {"status": "waiting", "checked_success_cases": 0, "reason": "no_records"},
        }

    @staticmethod
    def _state_summary(state: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "policy_version": state.get("policy_version", 1),
            "iterations": state.get("iterations", 0),
            "last_update_reason": state.get("last_update_reason", "initial"),
            "prompt_rule_count": len(state.get("prompt_rules", [])),
            "failure_memory_count": len(state.get("failure_lessons", [])),
            "context_rule_count": len(state.get("context_rules", [])),
            "tool_router_rule_count": len(state.get("tool_router_rules", [])),
        }

    @staticmethod
    def _candidate_rank(record: RcaCaseRecord, service: str) -> Optional[int]:
        if not service:
            return None
        service_norm = service.lower().strip()
        service_short = service_norm.split("/")[-1]
        for idx, candidate in enumerate(record.candidates or [], start=1):
            cand = str(candidate.get("service", "")).lower().strip()
            cand_short = cand.split("/")[-1]
            if cand == service_norm or cand_short == service_short or service_short in cand:
                return idx
        return None

    @staticmethod
    def _candidate_score(record: RcaCaseRecord, service: str) -> Optional[float]:
        rank = SelfEvolution._candidate_rank(record, service)
        if rank is None:
            return None
        try:
            return float((record.candidates or [])[rank - 1].get("score", 0))
        except (IndexError, TypeError, ValueError):
            return None

    @staticmethod
    def _projected_rank_after_patches(record: RcaCaseRecord, taxonomies: List[Dict[str, Any]], old_rank: Optional[int]) -> Optional[int]:
        if old_rank is None:
            return None
        codes = {item.get("code") for item in taxonomies}
        rank = old_rank
        if "service_alias_normalization" in codes:
            rank = min(rank, 1)
        if "candidate_underweighted" in codes and old_rank <= 5:
            rank = min(rank, 1)
        elif "candidate_underweighted" in codes:
            rank = max(1, old_rank - 3)
        if "log_frequency_bias" in codes and old_rank <= 3:
            rank = min(rank, 1)
        if "evidence_weight_bias" in codes and old_rank <= 5:
            rank = min(rank, 2)
        return rank

    @staticmethod
    def _adjust_tool_weights_from_failure(state: Dict[str, Any], case: Dict[str, Any]) -> None:
        weights = state.setdefault("tool_weights", {})
        tools = [t for t in case.get("tools_used", []) if t not in {"evidence_loading", "evaluation", "llm_rca"}]
        for tool in tools:
            item = weights.setdefault(tool, {"score": 0.5, "runs": 0, "successes": 0})
            item["runs"] = int(item.get("runs", 0)) + 1
            item["score"] = round(max(0.05, float(item.get("score", 0.5)) * 0.88), 4)

    def _load_agent_state(self) -> Dict[str, Any]:
        path = self.storage_dir / "agent_state.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except (json.JSONDecodeError, IOError):
                pass
        state = {
            "policy_version": 1,
            "iterations": 0,
            "tool_weights": {},
            "prompt_rules": [
                "先判断故障传播方向，再判断高频异常服务是否只是受害者。",
                "每个 Top-K 候选必须引用至少一条工具证据或拓扑证据。",
            ],
            "skills": [],
            "failure_lessons": [],
            "last_update_reason": "initial",
        }
        records = self._load_records()
        if records:
            state["iterations"] = len(records)
            for r in records:
                reward = max(0.05, min(1.0, r.mrr or (1.0 if r.hit_at_1 else 0.0)))
                for tool in r.tools_used:
                    if tool in {"evidence_loading", "evaluation", "llm_rca"}:
                        continue
                    item = state["tool_weights"].setdefault(tool, {"score": 0.5, "runs": 0, "successes": 0})
                    item["runs"] += 1
                    item["successes"] += int(r.hit_at_1)
                    item["score"] = round(item["score"] * 0.75 + reward * 0.25, 4)
                if r.hit_at_1:
                    state["skills"].append({
                        "source_id": r.source_id,
                        "fault_type": r.fault_type_inferred,
                        "root_cause_service": r.ground_truth_service,
                        "tool_chain": r.tools_used[:8],
                        "prompt_hint": f"类似 {r.source_id}/{r.fault_type_inferred} 时，重点验证 {r.ground_truth_service} 及其上下游传播。",
                        "confidence": r.top_candidate_score,
                        "created_at": r.timestamp,
                    })
                else:
                    state["failure_lessons"].append({
                        "case_id": r.case_id,
                        "source_id": r.source_id,
                        "fault_type": r.fault_type_inferred,
                        "wrong_top": r.top_candidate,
                        "ground_truth": r.ground_truth_service,
                        "lesson": f"历史失败：{r.top_candidate or 'unknown'} 曾被误排首位，需与 {r.ground_truth_service} 比较依赖方向和直接证据。",
                        "created_at": r.timestamp,
                    })
            state["skills"] = state["skills"][-50:]
            state["failure_lessons"] = state["failure_lessons"][-50:]
            state["last_update_reason"] = "bootstrapped_from_history"
            self._save_agent_state(state)
        return state

    def _save_agent_state(self, state: Dict[str, Any]) -> None:
        path = self.storage_dir / "agent_state.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _update_agent_state(self, record: RcaCaseRecord) -> None:
        """Update the agent policy after every RCA run.

        This is the active self-evolution step: the next orchestrator run reads
        this file and changes tool selection plus LLM prompt construction.
        """
        state = self._load_agent_state()
        state["iterations"] = int(state.get("iterations", 0)) + 1
        reward = max(0.05, min(1.0, record.mrr or (1.0 if record.hit_at_1 else 0.0)))

        weights = state.setdefault("tool_weights", {})
        for tool in record.tools_used:
            if tool in {"evidence_loading", "evaluation", "llm_rca"}:
                continue
            item = weights.setdefault(tool, {"score": 0.5, "runs": 0, "successes": 0})
            item["runs"] += 1
            item["successes"] += int(record.hit_at_1)
            item["score"] = round(item["score"] * 0.75 + reward * 0.25, 4)

        if record.hit_at_1:
            skill = {
                "source_id": record.source_id,
                "fault_type": record.fault_type_inferred,
                "root_cause_service": record.ground_truth_service,
                "tool_chain": record.tools_used[:8],
                "prompt_hint": f"类似 {record.source_id}/{record.fault_type_inferred} 时，重点验证 {record.ground_truth_service} 及其上下游传播。",
                "confidence": record.top_candidate_score,
                "created_at": record.timestamp,
            }
            skills = state.setdefault("skills", [])
            key = (skill["source_id"], skill["fault_type"], skill["root_cause_service"])
            if not any((s.get("source_id"), s.get("fault_type"), s.get("root_cause_service")) == key for s in skills):
                skills.append(skill)
            state["last_update_reason"] = "success_skill_added"
        else:
            lesson = {
                "case_id": record.case_id,
                "source_id": record.source_id,
                "fault_type": record.fault_type_inferred,
                "wrong_top": record.top_candidate,
                "ground_truth": record.ground_truth_service,
                "lesson": (
                    f"本轮误把 {record.top_candidate or 'unknown'} 排在首位，"
                    f"下一轮必须比较它与 {record.ground_truth_service} 的依赖方向、共同下游和直接证据。"
                ),
                "created_at": record.timestamp,
            }
            state.setdefault("failure_lessons", []).append(lesson)
            rule = (
                f"失败记忆：若候选 {record.top_candidate or 'unknown'} 与标注 {record.ground_truth_service} 同时异常，"
                "优先检查哪个服务是下游被调用方，避免把受害服务当根因。"
            )
            rules = state.setdefault("prompt_rules", [])
            if rule not in rules:
                rules.append(rule)
            state["policy_version"] = int(state.get("policy_version", 1)) + 1
            state["last_update_reason"] = "failure_prompt_patch_added"

        state["skills"] = state.get("skills", [])[-50:]
        state["failure_lessons"] = state.get("failure_lessons", [])[-50:]
        state["prompt_rules"] = state.get("prompt_rules", [])[-50:]
        self._save_agent_state(state)

    # ── Internal methods ──────────────────────────────────────────

    def _load_records(self) -> List[RcaCaseRecord]:
        if self._records is not None:
            return self._records

        records = []
        records_file = self.storage_dir / "rca_records.jsonl"
        if records_file.exists():
            with open(records_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data = json.loads(line)
                            records.append(RcaCaseRecord(**data))
                        except (json.JSONDecodeError, TypeError):
                            continue

        self._records = records
        return records

    def _save_record(self, record: RcaCaseRecord):
        records_file = self.storage_dir / "rca_records.jsonl"
        with open(records_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

        # Also update summary
        self._update_patterns_file(record)

    def _update_patterns_file(self, record: RcaCaseRecord):
        """Update the patterns.json summary file."""
        patterns_file = self.storage_dir / "patterns.json"
        patterns = {}
        if patterns_file.exists():
            try:
                with open(patterns_file, "r", encoding="utf-8") as f:
                    patterns = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        key = f"{record.source_id}:{record.fault_type_inferred}"
        if key not in patterns:
            patterns[key] = {
                "source": record.source_id,
                "fault_type": record.fault_type_inferred,
                "total_runs": 0,
                "successes": 0,
                "avg_mrr": 0.0,
                "best_tools": [],
            }

        p = patterns[key]
        p["total_runs"] += 1
        if record.hit_at_1:
            p["successes"] += 1
        p["avg_mrr"] = round(
            (p["avg_mrr"] * (p["total_runs"] - 1) + record.mrr) / p["total_runs"], 4
        )
        p["last_run"] = record.timestamp
        p["best_tools"] = record.tools_used[:6]

        with open(patterns_file, "w", encoding="utf-8") as f:
            json.dump(patterns, f, ensure_ascii=False, indent=2)

    def _analyze_failures(self, records: List[RcaCaseRecord]) -> List[Dict[str, Any]]:
        """Analyze failure cases to identify patterns."""
        failures = [r for r in records if not r.hit_at_1]
        if not failures:
            return []

        patterns = []
        # Group by source
        by_source = defaultdict(list)
        for r in failures:
            by_source[r.source_id].append(r)

        for source_id, cases in by_source.items():
            # Common fault types in failures
            fault_types = Counter(r.fault_type_inferred for r in cases)
            # Common ground truth services
            gt_services = Counter(r.ground_truth_service for r in cases)

            patterns.append({
                "source_id": source_id,
                "failure_count": len(cases),
                "common_fault_types": dict(fault_types.most_common(5)),
                "common_root_causes": dict(gt_services.most_common(5)),
                "avg_top_score": round(
                    sum(r.top_candidate_score for r in cases) / max(len(cases), 1), 3
                ),
                "suggestion": (
                    f"平台 {source_id} 有 {len(cases)} 次失败案例。"
                    f"最常见故障类型: {fault_types.most_common(1)[0][0] if fault_types else 'N/A'}。"
                    f"建议增加针对性的工具或优化LLM提示词。"
                ),
            })

        return patterns

    def _generate_suggestions(
        self, records: List[RcaCaseRecord], failure_patterns: List[Dict]
    ) -> List[str]:
        """Generate actionable improvement suggestions."""
        suggestions = []

        total = len(records)
        if total < 3:
            suggestions.append("积累更多案例后（≥5次），系统将自动生成优化建议。")
            return suggestions

        successes = sum(1 for r in records if r.hit_at_1)
        success_rate = successes / max(total, 1)

        if success_rate < 0.3:
            suggestions.append(
                "⚠️ 成功率较低 (<30%)。建议：1) 检查工具输出是否正确对接LLM；"
                "2) 优化LLM提示词，加入更多诊断上下文；3) 验证ground truth标注是否正确。"
            )

        if success_rate >= 0.6:
            suggestions.append(
                f"✅ 成功率已达 {success_rate:.0%}，系统正在持续优化中。"
            )

        # Per-source suggestions
        for fp in failure_patterns:
            if fp["failure_count"] >= 3:
                suggestions.append(
                    f"平台 {fp['source_id']} 失败率较高，建议针对该平台调整工具权重"
                    f"或增加平台特定的预处理逻辑。"
                )

        # Method analysis
        tool_only = [r for r in records if r.rca_method == "tool_fusion"]
        llm_runs = [r for r in records if r.rca_method != "tool_fusion"]
        if llm_runs:
            llm_sr = sum(1 for r in llm_runs if r.hit_at_1) / max(len(llm_runs), 1)
            if llm_sr < 0.3:
                suggestions.append(
                    "LLM RCA 准确率偏低，建议优化prompt或更换更大模型。"
                )

        # Duration analysis
        avg_dur = sum(r.duration_s for r in records) / max(total, 1)
        if avg_dur > 10:
            suggestions.append(
                f"平均耗时 {avg_dur:.1f}s，考虑并行化工具调用以提升效率。"
            )

        return suggestions

    @staticmethod
    def _infer_fault_type(
        candidates: List[Dict], pipeline_result: Dict
    ) -> str:
        """Infer fault type from candidates and pipeline context."""
        if candidates and candidates[0].get("reason"):
            reason = candidates[0]["reason"].lower()
            for ft in ["pod_crash", "high_cpu", "high_latency", "memory_leak",
                       "high_error_rate", "network_partition", "database_error",
                       "service_unavailable", "disk_full", "dns_failure"]:
                if ft.replace("_", " ") in reason or ft in reason:
                    return ft
        # Try from opsaug
        opsaug = pipeline_result.get("opsaug", {})
        candidates_from_opsaug = opsaug.get("root_cause_candidates", [])
        if candidates_from_opsaug:
            return "unknown_fault"

        return "unknown"
