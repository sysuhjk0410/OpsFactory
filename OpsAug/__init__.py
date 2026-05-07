"""art_tools: ART 异常检测、故障分类、根因定位工具集。"""

import os
import sys

# 确保 ART 项目根目录在 path 中，支持从任意工作目录导入
# opsaug_tools_v2 位于：
#   <repo>/tianchi-2025/project_3_algo_tools/opsaug_tools_v2
# ART 位于：
#   <repo>/tianchi-2025/project_3_algo_tools/opsaug_tools_v2/ART-master
_HERE = os.path.abspath(os.path.dirname(__file__))
_ART_ROOT = os.path.join(_HERE, "ART-master")
if _ART_ROOT not in sys.path:
    sys.path.insert(0, _ART_ROOT)
for _p in ["models", "models/diagnosis_tasks", "models/unified_representation"]:
    _full = os.path.join(_ART_ROOT, _p)
    if _full not in sys.path:
        sys.path.insert(0, _full)

from .config_tools import (
    load_art_config,
    load_cases,
    load_ad_labels,
    load_samples,
    hash_init,
)
from .model_tools import (
    train_representation,
    load_or_train_model,
)
from .diag_tools import (
    run_diag_workflow,
)
from .pipeline import (
    run_art_pipeline,
    run_opsaug_pipeline_from_long_modalities,
)
from .io_tools import (
    load_json,
    save_json,
    load_pkl,
    save_pkl,
)
from .data_fetcher import (
    fetch_k8s_metrics_long,
    fetch_apm_metrics_long,
    fetch_logs_long,
    fetch_traces_spans_long,
)
from .data_preprocess import (
    load_art_templates,
    build_art_samples_from_long_modalities,
    export_train_test_samples,
)

__all__ = [
    "load_art_config",
    "load_cases",
    "load_ad_labels",
    "load_samples",
    "hash_init",
    "train_representation",
    "load_or_train_model",
    "run_diag_workflow",
    "run_art_pipeline",
    "run_opsaug_pipeline_from_long_modalities",
    "load_json",
    "save_json",
    "load_pkl",
    "save_pkl",
    # data fetcher
    "fetch_k8s_metrics_long",
    "fetch_apm_metrics_long",
    "fetch_logs_long",
    "fetch_traces_spans_long",
    # data preprocess
    "load_art_templates",
    "build_art_samples_from_long_modalities",
    "export_train_test_samples",
]
