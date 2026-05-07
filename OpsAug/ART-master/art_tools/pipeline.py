"""端到端 ART 流水线：配置加载 → 模型训练/加载 → 诊断工作流 → 结果保存。"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

from .config_tools import (
    _get_art_root,
    hash_init,
    load_ad_labels,
    load_art_config,
    load_cases,
    load_samples,
)
from .diag_tools import run_diag_workflow
from .io_tools import save_json, save_pkl
from .model_tools import load_or_train_model


def run_art_pipeline(
    dataset: str = "D1",
    workflow: Optional[list[str]] = None,
    model_path: Optional[str] = None,
    res_dir: Optional[str] = None,
    use_timestamp_run: bool = True,
) -> dict[str, Any]:
    """端到端 ART 流水线：配置加载 → 模型训练/加载 → 诊断工作流 → 结果保存。

    Args:
        dataset: 数据集名，如 "D1"、"D2"。
        workflow: 诊断任务列表，如 ["AD", "FT", "RCL"]，默认全部。
        model_path: 模型保存/加载路径，默认 res/<dataset>/model.pkl。
        res_dir: 结果根目录，默认 res/<dataset>。
        use_timestamp_run: 是否使用时间戳子目录 runs/<timestamp>/。

    Returns:
        包含 config、model_path、run_dir、tmp_res、eval_res、res_path 的结果字典。
    """
    root = _get_art_root()
    config = load_art_config(dataset)
    config["dataset"] = config.get("dataset", dataset)

    cases = load_cases(config)
    ad_cases_label = load_ad_labels(config)
    node_hash, node_dict, type_hash, type_dict, channel_dict = hash_init(config)
    train_samples, test_samples = load_samples(config)

    if res_dir is None:
        res_dir = os.path.join(root, "res", dataset)
    os.makedirs(res_dir, exist_ok=True)

    if model_path is None:
        model_path = os.path.join(res_dir, "model.pkl")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(res_dir, "runs", run_id) if use_timestamp_run else res_dir
    tmp_dir = os.path.join(run_dir, "tmp")
    res_path = os.path.join(run_dir, "res.json")
    train_log_path = os.path.join(run_dir, "train_log.csv")

    model = load_or_train_model(
        config,
        model_path=model_path,
        train_log_path=train_log_path,
    )

    workflow = workflow or ["AD", "FT", "RCL"]
    tmp_res, eval_res = run_diag_workflow(
        config["downstream_param"],
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

    os.makedirs(tmp_dir, exist_ok=True)
    if "AD" in tmp_res:
        save_json(os.path.join(tmp_dir, "pre_interval.json"), tmp_res["AD"]["pre_interval"])
    if "FT" in tmp_res:
        save_json(os.path.join(tmp_dir, "pre_types.json"), tmp_res["FT"]["pre_types"])
    if "RCL" in tmp_res:
        tmp_res["RCL"]["rank_df"].to_csv(os.path.join(tmp_dir, "rank_df.csv"), index=False)

    save_json(res_path, eval_res)

    return {
        "config": config,
        "model_path": model_path,
        "run_dir": run_dir,
        "tmp_dir": tmp_dir,
        "tmp_res": tmp_res,
        "eval_res": eval_res,
        "res_path": res_path,
    }
