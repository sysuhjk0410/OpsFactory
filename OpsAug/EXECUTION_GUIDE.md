# OpsAug 三模态端到端执行指南

本文档说明如何在 `opsaug_tools_v2` 中执行以下流程：

1. 在线拉取三模态数据（metric / log / trace）
2. 对齐实例名并转换为 ART 的 D1 样本格式
3. 训练模型并执行 AD 评测

> 说明：当前流程使用 `opsaug_tools_v2/ART-master` 作为 ART 根目录。

---

## 1. 目录与前置条件

- 工具目录：`/SSF/dzj/tianchi-2025/project_3_algo_tools/opsaug_tools_v2`
- ART 目录：`/SSF/dzj/tianchi-2025/project_3_algo_tools/opsaug_tools_v2/ART-master`
- Python 环境：`douzj_opsaug`

### 1.1 确保 ART 数据存在

需要确保以下目录存在（至少 D1）：

- `opsaug_tools_v2/ART-master/data/D1/cases`
- `opsaug_tools_v2/ART-master/data/D1/hash_info`
- `opsaug_tools_v2/ART-master/data/D1/samples`

若为空，可从旧 ART 目录复制：

```bash
cp -r /SSF/dzj/OpsAug/ART-master/data/D1 /SSF/dzj/tianchi-2025/project_3_algo_tools/opsaug_tools_v2/ART-master/data/
cp -r /SSF/dzj/OpsAug/ART-master/data/D2 /SSF/dzj/tianchi-2025/project_3_algo_tools/opsaug_tools_v2/ART-master/data/
```

---

## 2. 环境与依赖

```bash
source /SSF/conda_install/etc/profile.d/conda.sh
conda activate douzj_opsaug
cd /SSF/dzj/tianchi-2025/project_3_algo_tools
```

### 2.1 安装阿里云 SDK（首次）

```bash
pip install \
  alibabacloud-cms20240330 \
  alibabacloud-sls20201230 \
  alibabacloud-credentials \
  aliyun-log-python-sdk \
  python-dotenv
```

---

## 3. 配置在线拉取凭证与数据源

请在 shell 中设置以下环境变量（建议用你自己的 AK/SK，不要硬编码进代码）：

```bash
export SLS_PROJECT_NAME="your_project"
export SLS_LOGSTORE_NAME="your_logstore"
export SLS_REGION="cn-hongkong"
export WORKSPACE_NAME="rca-benchmark"

export ALIBABA_CLOUD_ACCESS_KEY_ID="your_ak_id"
export ALIBABA_CLOUD_ACCESS_KEY_SECRET="your_ak_secret"
```

---

## 4. 先做数据拉取烟雾测试

```bash
python - <<'PY'
import os
from opsaug_tools_v2.data_fetcher import (
    fetch_k8s_metrics_long,
    fetch_logs_long,
    fetch_traces_spans_long,
)

metric_df = fetch_k8s_metrics_long(
    from_time="now-10m", to_time="now",
    region="cn-hongkong", workspace=os.environ["WORKSPACE_NAME"],
    step="30s", limit=2000,
)
log_df = fetch_logs_long(
    from_time="now-10m", to_time="now",
    region=os.environ["SLS_REGION"],
    project=os.environ["SLS_PROJECT_NAME"],
    logstore=os.environ["SLS_LOGSTORE_NAME"],
    query="*", limit=2000,
)
trace_df = fetch_traces_spans_long(
    from_time="now-10m", to_time="now",
    region="cn-hongkong", workspace=os.environ["WORKSPACE_NAME"],
    domain="apm", entity_set_name="apm.service",
    trace_set_domain="apm", trace_set_name="apm.trace.common",
    limit=30, detail_limit=1000,
)

print("metric rows:", len(metric_df))
print("log rows:", len(log_df))
print("trace rows:", len(trace_df))
PY
```

如果三者里有某个是 0，先不要继续训练，先排查数据源配置。

---

## 5. 端到端执行（拉取 → 预处理 → 训练 → AD）

> 说明：该脚本使用当前已实现的启发式 instance 映射，把在线实例名映射到 D1 的 `node_dict`。

```bash
python - <<'PY'
import os
import re

from opsaug_tools_v2.data_fetcher import (
    fetch_k8s_metrics_long,
    fetch_logs_long,
    fetch_traces_spans_long,
)
from opsaug_tools_v2.data_preprocess import (
    load_art_templates,
    build_art_samples_from_long_modalities,
    export_train_test_samples,
)
from opsaug_tools_v2.config_tools import (
    load_art_config, load_cases, load_ad_labels, hash_init, load_samples,
)
from opsaug_tools_v2.model_tools import load_or_train_model
from opsaug_tools_v2.diag_tools import run_diag_workflow

# 1) 拉取 30 分钟在线数据
metric_df = fetch_k8s_metrics_long(
    from_time="now-30m", to_time="now",
    region="cn-hongkong", workspace=os.environ["WORKSPACE_NAME"],
    step="30s", limit=20000,
)
log_df = fetch_logs_long(
    from_time="now-30m", to_time="now",
    region=os.environ["SLS_REGION"],
    project=os.environ["SLS_PROJECT_NAME"],
    logstore=os.environ["SLS_LOGSTORE_NAME"],
    query="*", limit=5000,
)
trace_df = fetch_traces_spans_long(
    from_time="now-30m", to_time="now",
    region="cn-hongkong", workspace=os.environ["WORKSPACE_NAME"],
    domain="apm", entity_set_name="apm.service",
    trace_set_domain="apm", trace_set_name="apm.trace.common",
    limit=50, detail_limit=2000,
)
print("fetched:", len(metric_df), len(log_df), len(trace_df))

# 2) instance 映射（启发式）
node_dict = load_art_templates("D1")["node_dict"]
node_set = set(node_dict)
svc_to_nodes = {}
for n in node_dict:
    base = re.sub(r"-\\d+$", "", n)
    svc_to_nodes.setdefault(base, []).append(n)

def map_name(x):
    if x is None:
        return None
    s = str(x)
    if s in node_set:
        return s
    svc = s.split("-")[0]
    for c in [svc, svc + "service", svc.replace("_", "-"), svc.replace("-", "")]:
        if c in svc_to_nodes:
            return svc_to_nodes[c][0]
        for b, nodes in svc_to_nodes.items():
            if b.startswith(c) or c.startswith(b):
                return nodes[0]
    return None

for name, df in [("metric", metric_df), ("log", log_df), ("trace", trace_df)]:
    if df is not None and not df.empty:
        before = len(df)
        df["instance"] = df["instance"].map(map_name)
        df.dropna(subset=["instance"], inplace=True)
        print(name, "mapped", len(df), "/", before, "instances", df["instance"].nunique())

# 3) 生成 D1 样本
timestamps = sorted(set(((metric_df["time"].astype(int) // 60) * 60).tolist()))
built = build_art_samples_from_long_modalities(
    dataset="D1",
    metric_long_df=metric_df,
    log_long_df=log_df,
    trace_long_df=trace_df,
    timestamps=timestamps,
    bucket_seconds=60,
    split_ratio=0.6,
)
print("samples:", len(built["train_samples"]), len(built["test_samples"]))

sample_dir = "/SSF/dzj/tianchi-2025/project_3_algo_tools/opsaug_tools_v2/ART-master/data/online_fetch_30m/samples"
export_train_test_samples(built["train_samples"], built["test_samples"], sample_dir)
print("exported:", sample_dir)

# 4) 训练 + AD
config = load_art_config("D1")
config["path"]["sample_dir"] = sample_dir
config["model_param"]["epochs"] = 20
config["model_param"]["batch_size"] = 8
config["model_param"]["augment"] = {"name": "NoAug"}

cases = load_cases(config)
ad_cases_label = load_ad_labels(config)
node_hash, node_dict, type_hash, type_dict, channel_dict = hash_init(config)
train_samples, test_samples = load_samples(config)

model = load_or_train_model(
    config,
    model_path="/SSF/dzj/tianchi-2025/project_3_algo_tools/opsaug_tools_v2/ART-master/res/D1/online_fetch_model.pkl",
    force_retrain=True,
    train_log_path="/SSF/dzj/tianchi-2025/project_3_algo_tools/opsaug_tools_v2/ART-master/res/D1/online_fetch_train_log.csv",
)

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
    workflow=["AD"],
)
print("AD eval:", eval_res.get("AD"))
print("pred intervals:", len(tmp_res.get("AD", {}).get("pre_interval", [])))
PY
```

---

## 6. 结果如何判断

- 结构层（必须通过）：
  - `metric/log/trace` 行数 > 0（至少 metric > 0）
  - `samples train/test` 非 0
  - 训练能开始并结束（可 early stopping）
  - `pred intervals` >= 0（通常 > 0）

- 指标层（当前在线模式需谨慎解释）：
  - 如果沿用 D1 的历史标签做 AD 指标，`precision/recall/f1` 可能为 0（时间窗标签不匹配导致，非链路故障）。
  - 在线无标签场景更建议看 `pre_interval`、告警数量、趋势稳定性。

---

## 7. 常见问题排查

### Q1: `ModuleNotFoundError: alibabacloud_cms20240330`
- 未安装 SDK，执行第 2 节的 pip 安装。

### Q2: logs 为 0 行
- 检查：
  - `SLS_PROJECT_NAME` / `SLS_LOGSTORE_NAME` 是否正确
  - 时间窗口是否有数据（尝试 `now-1h`）
  - 查询语句是否过于严格（先用 `*`）

### Q3: trace 映射率低
- 当前是启发式映射（`serviceName/hostname/pid` -> D1 node）。
- 建议新增确定性映射表，按你的线上服务命名进行一一对应。

### Q4: AD 报除零/空标签
- 已在本地 patch 做了保护：空标签返回 0 指标，不会中断流程。

---

## 8. 推荐下一步

1. 增加 `instance_mapping.py`（确定性映射表）提高三模态对齐率。  
2. 为在线窗口提供对应故障标签，才能得到有意义的 AD/FT/RCL 指标。  
3. 在此基础上再扩展到 `workflow=["AD","FT","RCL"]` 全流程评测。

