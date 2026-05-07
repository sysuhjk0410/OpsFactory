"""模型训练与加载工具。"""
from __future__ import annotations

import os
from typing import Any

from .config_tools import load_samples
from .io_tools import load_pkl, save_pkl


def train_representation(
    samples: list,
    model_param: dict[str, Any],
    log_path: str | None = None,
):
    """训练统一表示模型。

    Args:
        samples: 训练样本列表。
        model_param: 模型超参（instance_dim, num_heads, tf_layers 等）。
        log_path: 可选训练日志 CSV 路径。

    Returns:
        训练好的 model。
    """
    from models.unified_representation.train import train
    return train(samples, model_param, log_path=log_path)


def load_or_train_model(
    config: dict[str, Any],
    model_path: str | None = None,
    force_retrain: bool = False,
    train_log_path: str | None = None,
):
    """加载已有模型，若不存在则训练并保存。

    Args:
        config: 完整配置字典（含 path.sample_dir, model_param, train_samples_num）。
        model_path: 模型保存路径，默认 res/<dataset>/model.pkl。
        force_retrain: 为 True 时忽略已有模型并重新训练。
        train_log_path: 训练日志路径。

    Returns:
        模型对象。
    """
    from .config_tools import _get_art_root

    root = _get_art_root()
    path_cfg = config.get("path", {})
    sample_dir = path_cfg.get("sample_dir", "data/D1/samples/")
    if not os.path.isabs(sample_dir):
        sample_dir = os.path.join(root, sample_dir)
    model_param = config.get("model_param", {})
    train_num = config.get("train_samples_num", "whole")

    train_samples, _ = load_samples(sample_dir)
    input_samples = (
        train_samples
        if train_num == "whole"
        else train_samples[: train_num if isinstance(train_num, int) else len(train_samples)]
    )

    if model_path is None:
        dataset = config.get("dataset", "D1").replace("dataset_name_", "").replace("dataset_", "")
        model_path = os.path.join(root, "res", dataset, "model.pkl")

    if not force_retrain and os.path.exists(model_path):
        return load_pkl(model_path)

    os.makedirs(os.path.dirname(model_path) or ".", exist_ok=True)
    model = train_representation(
        input_samples,
        model_param,
        log_path=train_log_path,
    )
    save_pkl(model_path, model)
    return model
