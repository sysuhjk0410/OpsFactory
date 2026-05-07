"""诊断工作流工具：AD / FT / RCL。"""
from __future__ import annotations

from typing import Any, Optional

from models.diagnosis_tasks.diag_workflow import diag_workflow as _diag_workflow


def run_diag_workflow(
    config_downstream: dict[str, Any],
    model,
    train_samples: list,
    test_samples: list,
    cases,
    ad_cases_label,
    node_dict: list,
    type_hash,
    type_dict,
    channel_dict=None,
    workflow: Optional[list[str]] = None,
) -> tuple[dict, dict]:
    """执行诊断工作流（AD / FT / RCL）。

    Args:
        config_downstream: downstream_param 配置（AD、FT、RCL 参数）。
        model: 统一表示模型。
        train_samples: 训练样本。
        test_samples: 测试样本。
        cases: 案例 DataFrame。
        ad_cases_label: AD 案例标签。
        node_dict: 节点字典。
        type_hash: 类型哈希。
        type_dict: 类型字典。
        channel_dict: 通道字典（可选）。
        workflow: 任务列表，如 ["AD", "FT", "RCL"]。

    Returns:
        (tmp_res, eval_res) 临时结果与评估结果。
    """
    workflow = workflow or ["AD", "FT", "RCL"]
    return _diag_workflow(
        config_downstream,
        model,
        train_samples,
        test_samples,
        cases,
        ad_cases_label,
        node_dict,
        type_hash,
        type_dict,
        channel_dict=channel_dict,
        workflow=workflow,
    )
