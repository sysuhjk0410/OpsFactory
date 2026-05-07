"""IO 工具：JSON、PKL 读写。"""
from __future__ import annotations

import json
import os
import pickle


def load_json(filepath: str):
    """加载 JSON 文件。"""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(filepath: str, data) -> None:
    """保存为 JSON 文件。"""
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_pkl(filepath: str):
    """加载 PKL 文件。"""
    with open(filepath, "rb") as f:
        return pickle.load(f)


def save_pkl(filepath: str, data) -> None:
    """保存为 PKL 文件。"""
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "wb") as f:
        pickle.dump(data, f)
