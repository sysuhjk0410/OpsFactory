"""配置与数据加载工具。"""
from __future__ import annotations

import os
from typing import Any

import pandas as pd
import yaml

from .io_tools import load_pkl


def load_art_config(dataset: str, config_dir: str | None = None) -> dict[str, Any]:
    """加载指定数据集的 YAML 配置。

    Args:
        dataset: 数据集名，如 "D1"、"D2"。
        config_dir: 配置目录，默认使用项目内 config/。

    Returns:
        配置字典。
    """
    root = _get_art_root()
    cfg_dir = config_dir or os.path.join(root, "config")
    path = os.path.join(cfg_dir, f"{dataset}.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.load(f, Loader=yaml.FullLoader)


def load_cases(config: dict[str, Any]) -> pd.DataFrame:
    """从配置中的 case_path 加载案例 CSV。"""
    path = config["path"]["case_path"]
    if not os.path.isabs(path):
        path = os.path.join(_get_art_root(), path)
    return pd.read_csv(path)


def load_ad_labels(config: dict[str, Any]) -> Any:
    """从配置中的 ad_case_path 加载 AD 案例标签。"""
    path = config["path"]["ad_case_path"]
    if not os.path.isabs(path):
        path = os.path.join(_get_art_root(), path)
    return load_pkl(path)


def load_samples(config: dict[str, Any] | str) -> tuple[list, list]:
    """加载训练与测试样本。

    Args:
        config: 配置字典（取 path.sample_dir）或 sample_dir 路径字符串。

    Returns:
        (train_samples, test_samples)。
    """
    if isinstance(config, str):
        sample_dir = config
    else:
        sample_dir = config["path"]["sample_dir"]
    if not os.path.isabs(sample_dir):
        sample_dir = os.path.join(_get_art_root(), sample_dir)
    train_samples = load_pkl(os.path.join(sample_dir, "train_samples.pkl"))
    test_samples = load_pkl(os.path.join(sample_dir, "test_samples.pkl"))
    return train_samples, test_samples


def hash_init(config: dict[str, Any] | str) -> tuple:
    """初始化 node_hash, node_dict, type_hash, type_dict, channel_dict。"""
    if isinstance(config, str):
        hash_dir = config
    else:
        hash_dir = config["path"]["hash_dir"]
    if not os.path.isabs(hash_dir):
        hash_dir = os.path.join(_get_art_root(), hash_dir)
    node_hash = load_pkl(os.path.join(hash_dir, "node_hash.pkl"))
    node_dict = list(node_hash)
    type_hash = load_pkl(os.path.join(hash_dir, "type_hash.pkl"))
    type_dict = load_pkl(os.path.join(hash_dir, "type_dict.pkl"))
    channel_dict = load_pkl(os.path.join(hash_dir, "channel_dict.pkl"))
    return node_hash, node_dict, type_hash, type_dict, channel_dict


def _get_art_root() -> str:
    # opsaug_tools_v2 位于：
    #   <repo>/tianchi-2025/project_3_algo_tools/opsaug_tools_v2
    # ART 位于：
    #   <repo>/tianchi-2025/project_3_algo_tools/opsaug_tools_v2/ART-master
    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.abspath(os.path.join(here, "ART-master"))
