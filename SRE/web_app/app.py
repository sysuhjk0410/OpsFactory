"""
update web
SRE Web Dashboard
FastAPI-based single-page application with SSE streaming.
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
import threading
import uuid
from collections import deque
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ── Setup Paths ──
APP_DIR = Path(__file__).parent
ROOT_DIR = APP_DIR.parent
sys.path.insert(0, str(ROOT_DIR))

from configs.config_loader import get_config
from tools import build_tool_registry, LLMClient
from agents import DetectionAgent, AlertAgent
from orchestrator.pipeline import Pipeline
from orchestrator.daemon import Daemon
from integrations import (
    FaultDatasetCollector,
    HermesSkillClawRCA,
    LangChainRCAMultiAgent,
    SkillHermesAIOpsHarness,
    CloudOpsBenchAdapter, OpsAugAdapter, PromCopilotAdapter,
    RcaOrchestrator,
    SelfEvolution,
    get_adapter as get_ds_adapter,
    list_all_sources, list_sources_by_type,
    inject_fault_on_platform,
    restore_fault_on_platform,
)
from integrations.base_data_source import DataSourceError
from integrations.fault_injection_schedule import injection_capability

logger = logging.getLogger(__name__)

# ── FastAPI App ──
app = FastAPI(title="Ops Factory Dashboard", version="5.0.0")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

# ── Shared State ──
_state = {
    "config": None,
    "pipeline": None,
    "daemon": None,
    "daemon_thread": None,
    "detection_signals": deque(maxlen=200),
    "rca_runs": {},  # run_id → dict
    "pipeline_logs": deque(maxlen=500),
    "daemon_logs": deque(maxlen=500),
    "sse_subscribers": [],
    "cloudops_selected_case": None,
    "local_model_process": None,
    "enterprise_rca_flows": [],
    "ops_consult_sessions": deque(maxlen=100),
    "guard_plans": {},
    "guard_targets": {},
    "local_model_started_at": 0,
}

_cloudops_state = {
    "adapter": None,
    "opsaug": None,
    "promcopilot": None,
}

LOCAL_MODEL_PROVIDER = "local"
LOCAL_MODEL_NAME = "Qwen/Qwen3-0.6B"
LOCAL_MODEL_BASE_URL = "http://127.0.0.1:8000/v1"
MODEL_CHAT_READY_WAIT_S = int(os.getenv("OPSFACTORY_MODEL_CHAT_READY_WAIT_S", "2"))


def _get_config():
    if _state["config"] is None:
        _state["config"] = get_config()
        _normalize_llm_config(_state["config"].llm)
    return _state["config"]


def _normalize_llm_config(llm_cfg):
    provider = str(getattr(llm_cfg, "provider", "") or LOCAL_MODEL_PROVIDER).lower()
    if provider in {"qwen", "local_qwen"}:
        provider = LOCAL_MODEL_PROVIDER
    if provider in {"openai", "openai-compatible"}:
        provider = "openai_compatible"
    if provider not in {"local", "openai_compatible", "anthropic"}:
        provider = LOCAL_MODEL_PROVIDER
    llm_cfg.provider = provider
    if provider == LOCAL_MODEL_PROVIDER:
        llm_cfg.base_url = getattr(llm_cfg, "base_url", "") or LOCAL_MODEL_BASE_URL
        llm_cfg.model = getattr(llm_cfg, "model", "") or LOCAL_MODEL_NAME
    elif provider == "anthropic":
        llm_cfg.base_url = (getattr(llm_cfg, "base_url", "") or "https://api.anthropic.com/v1").rstrip("/")
    else:
        llm_cfg.base_url = (getattr(llm_cfg, "base_url", "") or "").rstrip("/")
    return llm_cfg


def _set_runtime_llm_provider(provider: str, *, base_url: str = "", model: str = "", api_key: str = "", temperature=None, max_tokens=None):
    cfg = _get_config()
    provider = str(provider or LOCAL_MODEL_PROVIDER).lower()
    if provider in {"qwen", "local_qwen"}:
        provider = LOCAL_MODEL_PROVIDER
    if provider in {"openai", "openai-compatible"}:
        provider = "openai_compatible"
    if provider not in {"local", "openai_compatible", "anthropic"}:
        raise HTTPException(400, "Unsupported model provider")

    cfg.llm.provider = provider
    if provider == LOCAL_MODEL_PROVIDER:
        cfg.llm.api_key = ""
        cfg.llm.base_url = LOCAL_MODEL_BASE_URL
        cfg.llm.model = LOCAL_MODEL_NAME
    else:
        cfg.llm.api_key = str(api_key or "").strip()
        cfg.llm.base_url = str(base_url or ("https://api.anthropic.com/v1" if provider == "anthropic" else "")).strip().rstrip("/")
        cfg.llm.model = str(model or "").strip()
        if not cfg.llm.api_key:
            raise HTTPException(400, "请填写你自己的 API Key。Ops Factory 不提供内置 API。")
        if not cfg.llm.base_url:
            raise HTTPException(400, "请填写 API Base URL。")
        if not cfg.llm.model:
            raise HTTPException(400, "请填写模型名称。")
    if temperature is not None:
        cfg.llm.temperature = float(temperature)
    if max_tokens is not None:
        cfg.llm.max_tokens = int(max_tokens)
    _normalize_llm_config(cfg.llm)
    _state["pipeline"] = None
    return cfg.llm


def _llm_is_user_api(llm_cfg=None) -> bool:
    cfg = llm_cfg or _get_config().llm
    return getattr(cfg, "provider", LOCAL_MODEL_PROVIDER) in {"openai_compatible", "anthropic"}


def _llm_configured(llm_cfg=None) -> bool:
    cfg = llm_cfg or _get_config().llm
    if getattr(cfg, "provider", LOCAL_MODEL_PROVIDER) == LOCAL_MODEL_PROVIDER:
        return True
    return bool(getattr(cfg, "api_key", "") and getattr(cfg, "base_url", "") and getattr(cfg, "model", ""))


def _masked_key(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _llm_public_config(health: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = _get_config()
    provider = getattr(cfg.llm, "provider", LOCAL_MODEL_PROVIDER)
    return {
        "provider": provider,
        "provider_label": {
            "local": "本地 Qwen-0.6B",
            "openai_compatible": "用户自带 OpenAI-compatible API",
            "anthropic": "用户自带 Anthropic-compatible API",
        }.get(provider, provider),
        "model": cfg.llm.model,
        "base_url": cfg.llm.base_url,
        "configured": _llm_configured(cfg.llm),
        "api_key_set": bool(getattr(cfg.llm, "api_key", "")),
        "api_key_preview": _masked_key(getattr(cfg.llm, "api_key", "")) if _llm_is_user_api(cfg.llm) else "",
        "local_default": provider == LOCAL_MODEL_PROVIDER,
        "user_api": _llm_is_user_api(cfg.llm),
        "max_tokens": cfg.llm.max_tokens,
        "temperature": cfg.llm.temperature,
        "health": health,
    }


def _prepare_llm_for_request_sync(wait_s: int = 45) -> Dict[str, Any]:
    cfg = _get_config()
    _normalize_llm_config(cfg.llm)
    provider = getattr(cfg.llm, "provider", LOCAL_MODEL_PROVIDER)
    status = {
        "requested": True,
        "provider": provider,
        "configured": _llm_configured(cfg.llm),
        "base_url": cfg.llm.base_url,
        "model": cfg.llm.model,
        "available": False,
        "attempted_health_check": False,
        "health": None,
        "bootstrap": None,
        "error": "",
    }
    if not status["configured"]:
        status["error"] = "用户自带 API 配置不完整。"
        return status
    if provider == LOCAL_MODEL_PROVIDER:
        status["attempted_health_check"] = True
        health = _llm_health_sync()
        status["initial_health"] = health
        if not health.get("ok"):
            bootstrap = _ensure_local_model_server_sync(wait_s)
            status["bootstrap"] = bootstrap
            health = bootstrap.get("health", health)
        status["health"] = health
        status["available"] = bool(health.get("ok"))
        if not status["available"]:
            status["error"] = health.get("error") or "本地 Qwen-0.6B 服务未就绪。"
        return status

    status["available"] = True
    if provider == "openai_compatible":
        status["attempted_health_check"] = True
        health = _llm_health_sync()
        status["health"] = health
        if not health.get("ok"):
            status["health_warning"] = health.get("error") or "该 OpenAI-compatible API 未提供 /models 健康检查；将直接尝试对话请求。"
    else:
        status["health"] = {
            "ok": True,
            "provider": provider,
            "message": "Anthropic-compatible API will be validated by the chat request.",
        }
    return status


def _get_pipeline():
    if _state["pipeline"] is None:
        _state["pipeline"] = Pipeline(_get_config())
    return _state["pipeline"]


def _is_offline_mode() -> bool:
    cfg = _get_config()
    return bool(
        getattr(cfg.observability, "backend", "") == "alidata"
        and getattr(cfg.observability, "offline_mode", False)
    )


def _reset_alidata_state():
    """Clear cached AliData adapters so the next request rebuilds them."""
    _alidata_state["downloader"] = None
    _alidata_state["log_tool"] = None
    _alidata_state["trace_tool"] = None
    _alidata_state["metric_tool"] = None


def _refresh_runtime_dependencies():
    """Rebuild runtime objects that depend on mutable observability config."""
    _state["pipeline"] = None
    _reset_alidata_state()
    _cloudops_state["adapter"] = None
    _cloudops_state["opsaug"] = None
    _cloudops_state["promcopilot"] = None

    daemon = _state.get("daemon")
    if daemon:
        try:
            daemon.cfg = _get_config()
            daemon.pipeline = Pipeline(daemon.cfg)
            daemon.poll_interval = daemon.cfg.daemon.poll_interval_seconds
            daemon.dedup_ttl = daemon.cfg.daemon.dedup_ttl_seconds
            daemon.max_concurrent = daemon.cfg.daemon.max_concurrent_pipelines
            daemon.namespace = daemon.cfg.daemon.default_namespace
        except Exception as e:
            logger.warning("Failed to refresh daemon after config change: %s", e)


def _normalize_problem_id(problem_id: str) -> str:
    value = str(problem_id or "").strip()
    if value.startswith("problem_"):
        value = value[len("problem_"):]
    return value


def _resolve_cloudops_root() -> Path:
    cfg = _get_config()
    configured = getattr(getattr(cfg, "cloudopsbench", None), "root_dir", "../Cloud-OpsBench")
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = (ROOT_DIR / path).resolve()
    return path


def _get_cloudops_adapter() -> CloudOpsBenchAdapter:
    if _cloudops_state["adapter"] is None:
        _cloudops_state["adapter"] = CloudOpsBenchAdapter(str(_resolve_cloudops_root()))
    return _cloudops_state["adapter"]


def _get_opsaug_adapter() -> OpsAugAdapter:
    if _cloudops_state["opsaug"] is None:
        _cloudops_state["opsaug"] = OpsAugAdapter(_get_cloudops_adapter())
    return _cloudops_state["opsaug"]


def _get_promcopilot_adapter() -> PromCopilotAdapter:
    if _cloudops_state["promcopilot"] is None:
        _cloudops_state["promcopilot"] = PromCopilotAdapter(_get_cloudops_adapter())
    return _cloudops_state["promcopilot"]


def _llm_base_is_local(base_url: str) -> bool:
    value = str(base_url or "").lower()
    return "127.0.0.1" in value or "localhost" in value or "0.0.0.0" in value


def _llm_models_url(base_url: str) -> str:
    return str(base_url or "").rstrip("/") + "/models"


def _local_model_health_url(base_url: str) -> str:
    value = str(base_url or "").rstrip("/")
    if value.endswith("/v1"):
        value = value[:-3]
    return value + "/health"


def _local_model_port(base_url: str) -> int:
    from urllib.parse import urlparse

    parsed = urlparse(str(base_url or ""))
    return int(parsed.port or 8000)


def _stop_stale_local_model_listeners_sync(port: int) -> List[int]:
    """Stop unhealthy Ops Factory local-model listeners that survived an app restart."""
    stopped: List[int] = []
    try:
        found = subprocess.run(
            ["lsof", "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return stopped

    for raw_pid in found.stdout.split():
        try:
            pid = int(raw_pid)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        try:
            inspected = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except Exception:
            continue
        if "local_model_server.py" not in inspected.stdout:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except OSError:
            continue

    if stopped:
        time.sleep(1)
        for pid in stopped:
            try:
                os.kill(pid, 0)
            except OSError:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return stopped


def _llm_health_sync() -> Dict[str, Any]:
    import urllib.request
    cfg = _get_config()
    provider = getattr(cfg.llm, "provider", LOCAL_MODEL_PROVIDER)
    if provider == "anthropic":
        return {
            "ok": _llm_configured(cfg.llm),
            "provider": provider,
            "url": cfg.llm.base_url,
            "model": cfg.llm.model,
            "message": "Anthropic-compatible endpoint will be checked on first request.",
        }
    if provider == LOCAL_MODEL_PROVIDER:
        url = _local_model_health_url(cfg.llm.base_url)
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
            return {
                "ok": str(payload.get("status", "")).lower() == "ok",
                "provider": provider,
                "url": url,
                "model": payload.get("model") or cfg.llm.model,
                "max_new_tokens": payload.get("max_new_tokens"),
            }
        except Exception as e:
            return {"ok": False, "provider": provider, "url": url, "model": cfg.llm.model, "error": str(e)}
    url = _llm_models_url(cfg.llm.base_url)
    headers = {}
    if provider != LOCAL_MODEL_PROVIDER and getattr(cfg.llm, "api_key", ""):
        headers["Authorization"] = f"Bearer {cfg.llm.api_key}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
        return {
            "ok": True,
            "provider": provider,
            "url": url,
            "model": cfg.llm.model,
            "models": [m.get("id") for m in payload.get("data", []) if isinstance(m, dict)],
        }
    except Exception as e:
        return {"ok": False, "provider": provider, "url": url, "model": cfg.llm.model, "error": str(e)}


def _ensure_local_model_server_sync(wait_s: int = 45) -> Dict[str, Any]:
    """Start bundled Qwen-0.6B server when local endpoint is down."""
    health = _llm_health_sync()
    if health.get("ok"):
        return {"started": False, "health": health, "message": "Local model server already running."}

    cfg = _get_config()
    if getattr(cfg.llm, "provider", LOCAL_MODEL_PROVIDER) != LOCAL_MODEL_PROVIDER:
        return {"started": False, "health": health, "message": "Configured LLM endpoint is remote; auto-start skipped."}

    ops_root = ROOT_DIR.parent
    model_dir = ops_root / "models" / "Qwen" / "Qwen3-0.6B"
    if not (model_dir / "model.safetensors").exists():
        return {"started": False, "health": health, "message": f"Local model file not found: {model_dir}"}

    proc = _state.get("local_model_process")
    proc_running = bool(proc and getattr(proc, "poll", lambda: 1)() is None)
    recently_started = proc_running and (time.time() - float(_state.get("local_model_started_at") or 0) < 180)
    restarted_pids: List[int] = []

    if proc_running and not recently_started:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        _state["local_model_process"] = None
        proc_running = False

    if not proc_running:
        restarted_pids = _stop_stale_local_model_listeners_sync(_local_model_port(cfg.llm.base_url))
        python_bin = ops_root / ".venv" / "bin" / "python"
        if not python_bin.exists():
            python_bin = Path(sys.executable)
        env = os.environ.copy()
        cache_dir = ops_root / ".cache" / "huggingface"
        cache_dir.mkdir(parents=True, exist_ok=True)
        env.setdefault("HF_HOME", str(cache_dir))
        env.setdefault("TRANSFORMERS_CACHE", str(cache_dir))
        cmd = [
            str(python_bin),
            str(ROOT_DIR / "local_model_server.py"),
            "--model-path", str(model_dir),
            "--model-name", LOCAL_MODEL_NAME,
            "--host", "127.0.0.1",
            "--port", "8000",
        ]
        _state["local_model_process"] = subprocess.Popen(
            cmd,
            cwd=str(ops_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _state["local_model_started_at"] = time.time()

    deadline = time.time() + wait_s
    last = health
    while time.time() < deadline:
        time.sleep(1)
        last = _llm_health_sync()
        if last.get("ok"):
            message = "Local model server started."
            if restarted_pids:
                message = f"Local model server restarted after stopping stale listener(s): {restarted_pids}."
            return {"started": True, "health": last, "message": message}
    return {"started": True, "health": last, "message": "Local model server is still warming up."}


def _case_topology_payload(detail: Dict[str, Any]) -> Dict[str, Any]:
    graph = detail.get("service_graph", {}) or {}
    services = graph.get("services", []) or detail.get("service_inventory", []) or []
    edges = graph.get("edges", []) or []
    gt = detail.get("root_cause_ground_truth") or ""
    root_service = RcaOrchestrator._extract_service(gt) if gt else ""
    affected = set()
    for edge in edges:
        src = edge.get("source", "")
        tgt = edge.get("target", "")
        if root_service and src == root_service and tgt:
            affected.add(tgt)
        if root_service and tgt == root_service and src:
            affected.add(src)
    metrics = detail.get("metrics", {}) or {}
    logs = detail.get("logs", {}) or {}
    traces = detail.get("traces", {}) or {}
    alerts = detail.get("alerts", {}) or {}
    log_count = len(logs.get("entries", []) or [])
    trace_count = len(traces.get("spans", []) or traces.get("traces", []) or [])
    alert_count = alerts.get("alert_count", len(alerts.get("alerts", []) or []))
    metric_rows = metrics.get("series_summary") or metrics.get("raw_series") or []
    max_metric = max([_numeric_signal(m) for m in metric_rows if isinstance(m, dict)] or [0])
    base_impact = min(0.92, 0.28 + log_count * 0.012 + trace_count * 0.01 + alert_count * 0.08 + min(max_metric, 10) * 0.035)
    health_frames = []
    services_set = set(services)
    root = root_service if root_service != "unknown" else ""
    wave = [root] if root else []
    neighbors = sorted(affected)
    remaining = [s for s in services if s not in set(wave + neighbors)]
    frame_defs = [
        ("注入点", wave, "fault_core"),
        ("服务调用扩散", neighbors, "service_mesh"),
        ("运行时压力", remaining[: max(1, min(4, len(remaining)))], "runtime"),
        ("用户体验影响", services[: max(1, min(5, len(services)))], "user_impact"),
    ]
    for idx, (label, active, scope) in enumerate(frame_defs):
        if not active:
            continue
        radius = idx + 1
        impact = min(0.98, base_impact + idx * 0.13 + len(active) / max(len(services_set), 1) * 0.16)
        health_frames.append({
            "step": idx + 1,
            "label": label,
            "scope": scope,
            "active_services": active,
            "propagation_radius": radius,
            "system_health": round(max(0.05, 1.0 - impact), 3),
            "latency_pressure": round(impact * 100, 1),
            "error_pressure": round(min(100, impact * 75), 1),
        })
    layer_health = {
        "fault": health_frames[0]["system_health"] if health_frames else 1,
        "service": health_frames[1]["system_health"] if len(health_frames) > 1 else max(0.2, 1 - base_impact * 0.75),
        "runtime": health_frames[2]["system_health"] if len(health_frames) > 2 else max(0.2, 1 - base_impact * 0.55),
        "data": 0.45 if root and any(x in root.lower() for x in ["db", "mongo", "mysql", "rabbit", "queue"]) else max(0.35, 1 - base_impact * 0.45),
        "experience": health_frames[-1]["system_health"] if health_frames else max(0.2, 1 - base_impact * 0.65),
    }
    system_overview = {
        "title": "System Propagation Overview",
        "injection_point": root,
        "impact_score": round(base_impact, 3),
        "dimensions": [
            {"id": "fault", "label": "Fault Injection Point", "health": layer_health["fault"], "pressure": round(base_impact * 100, 1), "z": 0},
            {"id": "service", "label": "Service Mesh / Calls", "health": layer_health["service"], "pressure": round((1 - layer_health["service"]) * 100, 1), "z": 28},
            {"id": "runtime", "label": "Runtime / Kubernetes", "health": layer_health["runtime"], "pressure": round((1 - layer_health["runtime"]) * 100, 1), "z": 56},
            {"id": "data", "label": "Data / Message Plane", "health": layer_health["data"], "pressure": round((1 - layer_health["data"]) * 100, 1), "z": 84},
            {"id": "experience", "label": "User / Business Impact", "health": layer_health["experience"], "pressure": round((1 - layer_health["experience"]) * 100, 1), "z": 112},
        ],
        "signals": {
            "log_count": log_count,
            "trace_count": trace_count,
            "metric_count": len(metric_rows),
            "alert_count": alert_count,
            "max_metric_signal": round(max_metric, 3),
        },
        "propagation_paths": [
            {"from": "fault", "to": "service", "weight": 0.95, "label": "错误/延迟进入调用层"},
            {"from": "service", "to": "runtime", "weight": 0.72, "label": "重试、排队、资源压力"},
            {"from": "runtime", "to": "data", "weight": 0.62, "label": "连接池、消息堆积、存储读写压力"},
            {"from": "data", "to": "experience", "weight": 0.78, "label": "SLO、用户请求和业务指标受损"},
        ],
    }
    return {
        "services": services,
        "edges": edges,
        "root_service": root,
        "affected_services": sorted(affected),
        "ground_truth": gt,
        "system_overview": system_overview,
        "system_layers": system_overview["dimensions"],
        "propagation_frames": health_frames,
        "modalities_preview": {
            "logs": (logs.get("entries", []) or [])[:5],
            "traces": (traces.get("spans", []) or traces.get("traces", []) or [])[:5],
            "metrics": (metrics.get("series_summary", []) or metrics.get("raw_series", []) or [])[:8],
        },
    }


def _numeric_signal(item: Dict[str, Any]) -> float:
    for key in ("max", "value", "mean", "range"):
        try:
            return float(item.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
    return 0.0


def _synthesize_traces(detail: Dict[str, Any]) -> List[Dict[str, Any]]:
    traces = detail.get("traces", {}) or {}
    spans = traces.get("spans") or traces.get("traces") or []
    if spans:
        return spans[:50]
    graph = detail.get("service_graph", {}) or {}
    root = RcaOrchestrator._extract_service(detail.get("root_cause_ground_truth", ""))
    result = []
    now = int(time.time() * 1000)
    for idx, edge in enumerate((graph.get("edges") or [])[:30]):
        src, tgt = edge.get("source", ""), edge.get("target", "")
        duration = 80 + idx * 7
        if root and (src == root or tgt == root):
            duration *= 12
        result.append({
            "trace_id": f"synth-{detail.get('case_id', 'case')}-{idx // 5}",
            "span_id": f"span-{idx}",
            "parent_span_id": f"span-{idx - 1}" if idx else "",
            "service": tgt or src,
            "operation": f"{edge.get('call_type', 'call')} {src}->{tgt}",
            "duration_ms": duration,
            "timestamp": now - idx * 1000,
            "synthetic": True,
        })
    return result


def _case_evidence_payload(detail: Dict[str, Any]) -> Dict[str, Any]:
    metrics = detail.get("metrics", {}) or {}
    logs = detail.get("logs", {}) or {}
    alerts = detail.get("alerts", {}) or {}
    raw_metrics = metrics.get("raw_series") or []
    summary_metrics = metrics.get("series_summary") or []
    top_metrics = sorted(
        [m for m in (summary_metrics or raw_metrics) if isinstance(m, dict)],
        key=_numeric_signal,
        reverse=True,
    )[:12]
    return {
        "case_id": detail.get("case_id"),
        "case_name": detail.get("case_name"),
        "source": detail.get("source"),
        "raw": {
            "logs": (logs.get("entries", []) or [])[:80],
            "traces": _synthesize_traces(detail),
            "metrics": {
                "series_summary": summary_metrics[:80],
                "raw_series": raw_metrics[:120],
                "top_metrics": top_metrics,
            },
            "alerts": (alerts.get("alerts", []) or [])[:50],
            "k8s_states": detail.get("k8s_states", {}),
        },
        "counts": {
            "logs": len(logs.get("entries", []) or []),
            "traces": len(_synthesize_traces(detail)),
            "metrics": len(summary_metrics or raw_metrics),
            "alerts": alerts.get("alert_count", len(alerts.get("alerts", []) or [])),
        },
    }


def _primary_candidates(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rca = result.get("rca_result") or result
    candidates = rca.get("parsed_candidates") or rca.get("candidates") or []
    return [c for c in candidates if isinstance(c, dict)]


def _markdown_table(rows: List[List[Any]], headers: List[str]) -> str:
    table = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        table.append("| " + " | ".join(str(cell).replace("\n", " ") for cell in row) + " |")
    return "\n".join(table)


def _build_diagnostic_report(run: Dict[str, Any]) -> str:
    """Build a downloadable ops diagnostic document for any RCA path."""

    result = run.get("result") or {}
    rca = result.get("rca_result") or {}
    eval_data = result.get("evaluation") or {}
    candidates = _primary_candidates(result)
    top = candidates[0] if candidates else {}
    source_id = run.get("source_id") or result.get("source_id") or "-"
    case_id = run.get("case_id") or result.get("case_id") or "-"
    path = "Hermes RCA Agent" if "Hermes RCA" in str(run.get("query", "")) else "Multi-Agent RCA"
    llm_status = rca.get("llm_status") or result.get("llm_status") or {}
    recovery = run.get("recovery_result") or result.get("recovery_result") or {}

    candidate_rows = [
        [
            c.get("rank", idx + 1),
            c.get("service", "-"),
            c.get("score", "-"),
            c.get("reason", "-"),
        ]
        for idx, c in enumerate(candidates[:10])
    ]
    agent_steps = (result.get("multiagent_diagnosis") or {}).get("steps") or result.get("stages") or []
    step_rows = [
        [
            idx + 1,
            step.get("name") or step.get("title") or step.get("agent_id") or step.get("id") or "-",
            step.get("status", "completed"),
            step.get("analysis") or step.get("explanation") or step.get("output_title") or "-",
        ]
        for idx, step in enumerate(agent_steps[:12])
        if isinstance(step, dict)
    ]

    lines = [
        "# Ops Factory 运维诊断文档",
        "",
        f"- 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Run ID: {run.get('id', '-')}",
        f"- 诊断路径: {path}",
        f"- 数据源: {source_id}",
        f"- Case: {case_id}",
        f"- 状态: {run.get('status', result.get('status', '-'))}",
        "",
        "## 结论",
        "",
        f"- Top1 根因候选: {top.get('service', '-')}",
        f"- 置信分: {top.get('score', '-')}",
        f"- 判断依据: {top.get('reason', '-')}",
        f"- Ground Truth: {result.get('ground_truth', '-')}",
        "",
        "## 评估",
        "",
        _markdown_table(
            [[k, eval_data.get(k, "-")] for k in ("ACC@1", "ACC@3", "ACC@5", "ACC@10", "MRR")],
            ["指标", "值"],
        ),
        "",
        "## 根因候选",
        "",
        _markdown_table(candidate_rows or [["-", "-", "-", "未生成候选"]], ["Rank", "Service", "Score", "Reason"]),
        "",
        "## Agent 执行摘要",
        "",
        _markdown_table(step_rows or [["-", "-", "-", "未记录 Agent 执行轨迹"]], ["Step", "Agent/Stage", "Status", "Output"]),
        "",
        "## LLM 与工具状态",
        "",
        "```json",
        json.dumps(
            {
                "llm_used": rca.get("llm_used", False),
                "fallback_used": rca.get("fallback_used", False),
                "llm_status": llm_status,
                "selected_tools": result.get("selected_tools") or result.get("tools_used") or [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## 恢复校验",
        "",
        "```json",
        json.dumps(recovery or {"status": "not_triggered", "message": "诊断报告生成时尚未执行故障恢复。"}, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 建议",
        "",
        "- 若为动态注入故障，必须以恢复面板中的 Kubernetes readiness 校验为准。",
        "- 若 Top1 未命中，请进入成效看板的失败归因与改进流程，发布新的上下文、Prompt 或工具路由补丁后再 replay。",
    ]
    return "\n".join(lines)


def _report_font_path() -> Optional[str]:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for item in candidates:
        if Path(item).exists():
            return item
    return None


def _build_diagnostic_pdf(run: Dict[str, Any]) -> bytes:
    """Render a polished multi-page PDF report with Pillow only."""

    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:  # pragma: no cover - environment guard
        raise HTTPException(500, f"PDF generation requires Pillow: {exc}") from exc

    result = run.get("result") or {}
    rca = result.get("rca_result") or {}
    eval_data = result.get("evaluation") or {}
    candidates = _primary_candidates(result)
    top = candidates[0] if candidates else {}
    source_id = run.get("source_id") or result.get("source_id") or "-"
    case_id = run.get("case_id") or result.get("case_id") or "-"
    path = "Hermes RCA Agent" if "Hermes RCA" in str(run.get("query", "")) else "Multi-Agent RCA"
    llm_status = rca.get("llm_status") or result.get("llm_status") or {}
    recovery = run.get("recovery_result") or result.get("recovery_result") or {}
    agent_steps = (result.get("multiagent_diagnosis") or {}).get("steps") or result.get("stages") or []

    page_w, page_h = 1240, 1754
    margin = 86
    content_w = page_w - margin * 2
    navy = (17, 24, 39)
    ink = (30, 41, 59)
    muted = (100, 116, 139)
    border = (226, 232, 240)
    accent = (37, 99, 235)
    cyan = (8, 145, 178)
    green = (22, 163, 74)
    amber = (217, 119, 6)
    soft = (248, 250, 252)
    font_path = _report_font_path()

    def font(size: int):
        if font_path:
            try:
                return ImageFont.truetype(font_path, size, index=0)
            except TypeError:
                return ImageFont.truetype(font_path, size)
        return ImageFont.load_default()

    f_title = font(48)
    f_h1 = font(30)
    f_h2 = font(24)
    f_body = font(21)
    f_small = font(17)
    f_tiny = font(15)

    pages: List[Any] = []
    page_no = 0
    img = None
    draw = None
    y = margin

    def start_page(first: bool = False):
        nonlocal img, draw, y, page_no
        img = Image.new("RGB", (page_w, page_h), "white")
        draw = ImageDraw.Draw(img)
        page_no += 1
        pages.append(img)
        draw.rectangle([0, 0, page_w, 72], fill=navy)
        draw.text((margin, 22), "Ops Factory RCA Diagnostic Report", font=f_small, fill=(226, 232, 240))
        draw.text((page_w - margin - 110, 22), f"Page {page_no}", font=f_small, fill=(148, 163, 184))
        if first:
            y = 132
        else:
            y = 112

    def text_width(text: str, ft) -> int:
        bbox = draw.textbbox((0, 0), str(text), font=ft)
        return bbox[2] - bbox[0]

    def wrap_text(text: Any, ft, max_width: int) -> List[str]:
        raw = str(text if text is not None else "-")
        lines: List[str] = []
        for paragraph in raw.splitlines() or [""]:
            current = ""
            for ch in paragraph:
                if text_width(current + ch, ft) <= max_width or not current:
                    current += ch
                else:
                    lines.append(current)
                    current = ch
            lines.append(current or " ")
        return lines

    def ellipsize_text(text: Any, ft, max_width: int) -> str:
        value = str(text if text is not None else "-")
        if text_width(value, ft) <= max_width:
            return value
        suffix = "..."
        current = ""
        for ch in value:
            if text_width(current + ch + suffix, ft) > max_width:
                break
            current += ch
        return (current or value[:1]) + suffix

    def limit_wrapped_lines(text: Any, ft, max_width: int, max_lines: int) -> List[str]:
        lines = wrap_text(text, ft, max_width)
        if len(lines) <= max_lines:
            return lines
        kept = lines[:max_lines]
        kept[-1] = ellipsize_text(kept[-1], ft, max_width)
        return kept

    def ensure_space(height: int):
        nonlocal y
        if y + height > page_h - margin:
            start_page(False)

    def draw_wrapped(text: Any, x: int, max_width: int, ft, fill=ink, line_gap: int = 8) -> int:
        nonlocal y
        lines = wrap_text(text, ft, max_width)
        line_h = ft.size + line_gap if hasattr(ft, "size") else 25
        for line in lines:
            ensure_space(line_h + 8)
            draw.text((x, y), line, font=ft, fill=fill)
            y += line_h
        return line_h * len(lines)

    def add_heading(text: str, level: int = 1):
        nonlocal y
        ft = f_h1 if level == 1 else f_h2
        color = navy if level == 1 else accent
        ensure_space(72)
        if level == 1:
            draw.rounded_rectangle([margin, y, margin + 12, y + 42], radius=6, fill=accent)
            draw.text((margin + 24, y + 2), text, font=ft, fill=color)
            y += 62
        else:
            draw.text((margin, y), text, font=ft, fill=color)
            y += 44

    def add_para(text: Any, tone=ink):
        nonlocal y
        draw_wrapped(text, margin, content_w, f_body, tone, 8)
        y += 10

    def add_chip_row(items: List[Dict[str, Any]]):
        nonlocal y
        chip_h = 128
        ensure_space(chip_h + 26)
        gap = 16
        chip_w = (content_w - gap * (len(items) - 1)) // max(1, len(items))
        x = margin
        for item in items:
            inner_w = chip_w - 44
            draw.rounded_rectangle([x, y, x + chip_w, y + chip_h], radius=18, fill=soft, outline=border, width=2)
            draw.text((x + 22, y + 18), ellipsize_text(item.get("label", ""), f_tiny, inner_w), font=f_tiny, fill=muted)
            value_lines = limit_wrapped_lines(item.get("value", "-"), f_small, inner_w, 2)
            vy = y + 52
            for line in value_lines:
                draw.text((x + 22, vy), line, font=f_small, fill=item.get("color", ink))
                vy += 25
            x += chip_w + gap
        y += chip_h + 26

    def add_table(headers: List[str], rows: List[List[Any]], widths: List[float]):
        nonlocal y
        col_w = [int(content_w * w) for w in widths]
        col_w[-1] += content_w - sum(col_w)
        row_gap = 12
        ensure_space(64)
        x = margin
        draw.rounded_rectangle([margin, y, margin + content_w, y + 42], radius=10, fill=(239, 246, 255))
        for idx, head in enumerate(headers):
            draw.text((x + 12, y + 10), head, font=f_tiny, fill=accent)
            x += col_w[idx]
        y += 48
        for ridx, row in enumerate(rows):
            wrapped = [
                limit_wrapped_lines(cell, f_tiny, max(40, col_w[idx] - 24), 5)
                for idx, cell in enumerate(row)
            ]
            row_h = max(52, max(len(cell_lines) for cell_lines in wrapped) * 23 + row_gap)
            ensure_space(row_h + 8)
            fill = (255, 255, 255) if ridx % 2 == 0 else soft
            draw.rounded_rectangle([margin, y, margin + content_w, y + row_h], radius=8, fill=fill, outline=border)
            x = margin
            for idx, cell_lines in enumerate(wrapped):
                cy = y + 12
                for line in cell_lines:
                    draw.text((x + 12, cy), line, font=f_tiny, fill=ink)
                    cy += 23
                x += col_w[idx]
            y += row_h + 8
        y += 12

    start_page(True)
    draw.text((margin, y), "Ops Factory", font=f_small, fill=accent)
    y += 38
    draw.text((margin, y), "运维诊断文档", font=f_title, fill=navy)
    y += 68
    add_para("面向运维工程师的 RCA 结果报告，包含故障上下文、Agent 执行链路、候选根因、评估指标、模型/工具状态和恢复校验信息。", muted)
    add_chip_row([
        {"label": "Run ID", "value": run.get("id", "-"), "color": accent},
        {"label": "诊断路径", "value": path, "color": cyan},
        {"label": "数据源", "value": source_id, "color": ink},
        {"label": "Case", "value": case_id, "color": ink},
    ])

    add_heading("1. 诊断摘要", 1)
    add_heading("1.1 核心结论", 2)
    add_chip_row([
        {"label": "Top1 根因候选", "value": top.get("service", "-"), "color": accent},
        {"label": "置信分", "value": top.get("score", "-"), "color": green},
        {"label": "Ground Truth", "value": result.get("ground_truth", "-"), "color": amber},
    ])
    add_para(f"判断依据：{top.get('reason', '未生成候选依据')}")

    add_heading("1.2 评估指标", 2)
    add_table(
        ["指标", "值", "说明"],
        [[k, eval_data.get(k, "-"), "按候选排名评估，不代表系统永远百分百正确。"] for k in ("ACC@1", "ACC@3", "ACC@5", "ACC@10", "MRR")],
        [0.18, 0.18, 0.64],
    )

    add_heading("2. 根因候选", 1)
    add_table(
        ["Rank", "Service", "Score", "Reason"],
        [[c.get("rank", idx + 1), c.get("service", "-"), c.get("score", "-"), c.get("reason", "-")] for idx, c in enumerate(candidates[:10])] or [["-", "-", "-", "未生成候选"]],
        [0.12, 0.22, 0.16, 0.50],
    )

    add_heading("3. Agent 执行链路", 1)
    step_rows = [
        [
            idx + 1,
            step.get("name") or step.get("title") or step.get("agent_id") or step.get("id") or "-",
            step.get("status", "completed"),
            step.get("analysis") or step.get("explanation") or step.get("output_title") or "-",
        ]
        for idx, step in enumerate(agent_steps[:14])
        if isinstance(step, dict)
    ]
    add_table(["Step", "Agent/Stage", "Status", "关键输出"], step_rows or [["-", "-", "-", "未记录 Agent 执行轨迹"]], [0.10, 0.25, 0.16, 0.49])

    add_heading("4. 模型与工具状态", 1)
    add_table(
        ["项目", "状态"],
        [
            ["LLM 使用", "已使用" if rca.get("llm_used") else "未使用或未产出可解析候选"],
            ["Fallback", "是" if rca.get("fallback_used") else "否"],
            ["模型", llm_status.get("model") or rca.get("model") or "-"],
            ["错误/说明", llm_status.get("error") or "无"],
            ["选中工具", ", ".join(result.get("selected_tools") or result.get("tools_used") or []) or "-"],
        ],
        [0.24, 0.76],
    )

    add_heading("5. 恢复校验与处置建议", 1)
    add_heading("5.1 恢复校验", 2)
    recovery_summary = recovery or {"status": "not_triggered", "message": "诊断报告生成时尚未执行故障恢复。"}
    add_para(json.dumps(recovery_summary, ensure_ascii=False, indent=2), muted)
    add_heading("5.2 建议", 2)
    add_para("若为动态注入故障，请以恢复面板中的 Kubernetes readiness 校验为准；只有 actual_cluster_recovery=true 且 restore_verified=true 时才视为真正恢复。")
    add_para("若 Top1 未命中，请进入成效看板的失败归因与改进流程，发布新的上下文、Prompt 或工具路由补丁后再 replay。")

    out = BytesIO()
    pages[0].save(out, format="PDF", save_all=True, append_images=pages[1:], resolution=150.0)
    return out.getvalue()


def _enterprise_flow_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    name = str(body.get("name") or body.get("flow_name") or "").strip()
    if not name:
        raise HTTPException(400, "Missing enterprise RCA flow name")
    flow = {
        "id": body.get("id") or f"enterprise-rca-{uuid.uuid4().hex[:8]}",
        "name": name,
        "endpoint": str(body.get("endpoint") or body.get("entrypoint") or "").strip(),
        "algorithm_type": str(body.get("algorithm_type") or "rca_workflow").strip(),
        "description": str(body.get("description") or "").strip(),
        "input_modalities": body.get("input_modalities") or ["logs", "traces", "metrics", "topology"],
        "trigger_condition": str(body.get("trigger_condition") or "由工具路由智能体根据当前故障数据重新评估").strip(),
        "output_contract": str(body.get("output_contract") or "ranked RCA candidates + evidence summary + confidence").strip(),
        "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "registered",
    }
    return flow


def _consult_summary_from_case(detail: Dict[str, Any]) -> Dict[str, Any]:
    evidence = _case_evidence_payload(detail)
    topology = _case_topology_payload(detail)
    top_metrics = evidence.get("raw", {}).get("metrics", {}).get("top_metrics", [])[:5]
    logs = evidence.get("raw", {}).get("logs", [])[:3]
    traces = evidence.get("raw", {}).get("traces", [])[:3]
    return {
        "case": detail.get("case_name") or detail.get("case_id") or "-",
        "root_hint": topology.get("root_service") or "-",
        "counts": evidence.get("counts", {}),
        "top_metrics": top_metrics,
        "log_samples": logs,
        "trace_samples": traces,
        "affected_services": topology.get("affected_services", [])[:8],
    }


def _make_guard_plan(objective: str, scope: str, cadence: str, risk_level: str) -> Dict[str, Any]:
    plan_id = f"guard-{uuid.uuid4().hex[:8]}"
    high_risk = risk_level in {"high", "critical"}
    plan = {
        "id": plan_id,
        "name": objective[:42] or "持续守护计划",
        "objective": objective or "持续观察服务健康度并提前暴露 RCA 风险",
        "scope": scope or "all connected platforms",
        "cadence": cadence or "manual",
        "risk_level": risk_level or "medium",
        "status": "draft",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_action": "计划已生成，等待执行一次或接入自动触发策略。",
        "human_confirm_required": high_risk,
        "execution_map": [
            {"step": "观测快照", "purpose": "采集指标、日志、链路和拓扑健康摘要", "gate": False},
            {"step": "风险归因", "purpose": "用记忆和工具收益判断是否需要进入 RCA 预热", "gate": False},
            {"step": "处置建议", "purpose": "生成恢复建议和风险提示", "gate": high_risk},
            {"step": "报告沉淀", "purpose": "输出守护报告并写入长期记忆", "gate": False},
        ],
        "reports": [],
    }
    _state["guard_plans"][plan_id] = plan
    return plan


def _normalize_guard_endpoint(endpoint: str, port: str, health_path: str) -> str:
    endpoint = (endpoint or "").strip()
    port = (port or "").strip()
    health_path = (health_path or "/health").strip() or "/health"
    if health_path and not health_path.startswith("/"):
        health_path = "/" + health_path
    if endpoint and not endpoint.startswith(("http://", "https://")):
        endpoint = "http://" + endpoint
    if endpoint and port and ":" not in endpoint.split("//", 1)[-1].split("/", 1)[0]:
        endpoint = endpoint.rstrip("/") + f":{port}"
    return (endpoint.rstrip("/") + health_path) if endpoint else ""


def _extract_guard_system_payload(payload: Any) -> Optional[Dict[str, Any]]:
    """Accept either a direct system payload or {"system": {...}} from enterprise endpoints."""

    import math

    if not isinstance(payload, dict):
        return None
    candidate = payload.get("system") if isinstance(payload.get("system"), dict) else payload
    if not isinstance(candidate.get("services"), list) or not isinstance(candidate.get("edges"), list):
        return None
    raw_services = candidate.get("services") or []
    services = []
    for idx, svc in enumerate(raw_services):
        if not isinstance(svc, dict):
            continue
        svc_id = str(svc.get("id") or svc.get("name") or f"service-{idx + 1}")
        risk = 0.18
        try:
            risk = max(0.0, min(1.0, float(svc.get("risk", risk))))
        except Exception:
            pass
        status = str(svc.get("status") or ("critical" if risk >= 0.82 else "degraded" if risk >= 0.5 else "healthy")).strip()
        if status not in {"healthy", "degraded", "critical", "unknown"}:
            status = "unknown"
        layer = str(svc.get("layer") or "service").strip()
        if layer not in {"edge", "service", "infra", "data"}:
            layer = "service"
        angle = (idx / max(len(raw_services), 1)) * math.tau
        radius = 2.4 + (idx % 3) * 0.9
        fallback_position = {"x": round(math.cos(angle) * radius, 2), "y": 0.8, "z": round(math.sin(angle) * radius, 2)}
        position = svc.get("position") if isinstance(svc.get("position"), dict) else fallback_position
        def _coord(axis: str) -> float:
            try:
                return float(position.get(axis, fallback_position[axis]))
            except Exception:
                return float(fallback_position[axis])
        normalized = dict(svc)
        normalized.update({
            "id": svc_id,
            "name": str(svc.get("name") or svc_id),
            "layer": layer,
            "status": status,
            "risk": round(risk, 3),
            "position": {"x": _coord("x"), "y": _coord("y"), "z": _coord("z")},
            "metrics": svc.get("metrics") if isinstance(svc.get("metrics"), dict) else {},
        })
        services.append(normalized)
    if not services:
        return None
    service_ids = {svc["id"] for svc in services}
    edges = []
    for edge in candidate.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in service_ids or target not in service_ids:
            continue
        normalized_edge = dict(edge)
        try:
            risk_flow = max(0.0, min(1.0, float(edge.get("risk_flow", 0.2))))
        except Exception:
            risk_flow = 0.2
        normalized_edge.update({"source": source, "target": target, "risk_flow": round(risk_flow, 3)})
        edges.append(normalized_edge)
    candidate["services"] = services
    candidate["edges"] = edges
    candidate.setdefault("id", f"enterprise-guard-{uuid.uuid4().hex[:8]}")
    candidate.setdefault("name", "企业真实系统")
    candidate.setdefault("scenario_name", "真实系统巡检")
    candidate.setdefault("status", "unknown")
    candidate.setdefault("root", services[0]["id"])
    candidate.setdefault("symptom", "企业系统返回了可观测模型，持续守护将按服务、依赖和风险状态巡检。")
    candidate.setdefault("slo", {})
    candidate.setdefault("events", [])
    return candidate


def _guard_simulated_system(scenario: str = "checkout_latency") -> Dict[str, Any]:
    """Built-in observability simulator used by Continuous Guard demos."""

    scenario = scenario or "checkout_latency"
    profiles = {
        "checkout_latency": {
            "name": "结算链路延迟扩散",
            "root": "checkout",
            "symptom": "订单提交 P95 延迟升高，并向 payment / inventory 扩散。",
            "risk": {"checkout": 0.88, "payment": 0.67, "inventory": 0.58, "gateway": 0.46},
            "latency": {"checkout": 820, "payment": 470, "inventory": 390, "gateway": 260},
            "error": {"checkout": 0.041, "payment": 0.022, "inventory": 0.018},
        },
        "catalog_error": {
            "name": "商品服务错误率升高",
            "root": "catalog",
            "symptom": "商品查询 5xx 上升，推荐与网关出现受害性抖动。",
            "risk": {"catalog": 0.91, "recommendation": 0.62, "gateway": 0.52},
            "latency": {"catalog": 540, "recommendation": 360, "gateway": 240},
            "error": {"catalog": 0.116, "recommendation": 0.033, "gateway": 0.024},
        },
        "node_pressure": {
            "name": "节点资源压力",
            "root": "node-a",
            "symptom": "节点 CPU/内存压力导致 checkout、catalog、inventory 同时波动。",
            "risk": {"node-a": 0.94, "checkout": 0.71, "catalog": 0.63, "inventory": 0.55},
            "latency": {"checkout": 610, "catalog": 430, "inventory": 390},
            "error": {"checkout": 0.027, "catalog": 0.021, "inventory": 0.018},
        },
        "payment_timeout": {
            "name": "支付依赖超时",
            "root": "payment",
            "symptom": "payment 调第三方支付超时，checkout 阻塞并触发订单失败。",
            "risk": {"payment": 0.93, "checkout": 0.76, "database": 0.54, "gateway": 0.43},
            "latency": {"payment": 960, "checkout": 720, "database": 430, "gateway": 310},
            "error": {"payment": 0.083, "checkout": 0.046, "database": 0.018},
        },
        "inventory_db_lock": {
            "name": "库存库锁等待",
            "root": "inventory",
            "symptom": "inventory 写入库存库出现锁等待，影响 checkout 与 shipping。",
            "risk": {"inventory": 0.9, "database": 0.78, "checkout": 0.66, "shipping": 0.52},
            "latency": {"inventory": 880, "database": 690, "checkout": 520, "shipping": 390},
            "error": {"inventory": 0.062, "database": 0.027, "checkout": 0.023},
        },
        "recommendation_storm": {
            "name": "推荐服务调用风暴",
            "root": "recommendation",
            "symptom": "recommendation 请求放大，拖慢 catalog 并挤压 gateway 线程池。",
            "risk": {"recommendation": 0.89, "catalog": 0.69, "gateway": 0.61, "node-a": 0.5},
            "latency": {"recommendation": 740, "catalog": 560, "gateway": 430},
            "error": {"recommendation": 0.054, "catalog": 0.031, "gateway": 0.019},
        },
        "gateway_traffic_spike": {
            "name": "入口流量突增",
            "root": "gateway",
            "symptom": "gateway 流量突增，用户、商品、购物车链路同时出现排队。",
            "risk": {"gateway": 0.92, "user": 0.62, "catalog": 0.58, "cart": 0.57},
            "latency": {"gateway": 680, "user": 420, "catalog": 410, "cart": 405},
            "error": {"gateway": 0.048, "user": 0.021, "catalog": 0.019, "cart": 0.018},
        },
        "shipping_queue_backlog": {
            "name": "物流队列积压",
            "root": "shipping",
            "symptom": "shipping 队列消费落后，订单完成链路出现尾延迟。",
            "risk": {"shipping": 0.86, "checkout": 0.59, "database": 0.5},
            "latency": {"shipping": 790, "checkout": 470, "database": 340},
            "error": {"shipping": 0.044, "checkout": 0.019},
        },
        "user_auth_error": {
            "name": "用户认证异常",
            "root": "user",
            "symptom": "user 会话校验异常，gateway 与 checkout 出现认证失败。",
            "risk": {"user": 0.88, "gateway": 0.66, "checkout": 0.52},
            "latency": {"user": 520, "gateway": 380, "checkout": 360},
            "error": {"user": 0.078, "gateway": 0.034, "checkout": 0.021},
        },
        "cart_cache_hotspot": {
            "name": "购物车缓存热点",
            "root": "cart",
            "symptom": "cart 热 key 放大导致 checkout 读写排队。",
            "risk": {"cart": 0.87, "checkout": 0.65, "gateway": 0.44},
            "latency": {"cart": 730, "checkout": 530, "gateway": 290},
            "error": {"cart": 0.052, "checkout": 0.024},
        },
        "database_disk_io": {
            "name": "数据库磁盘 IO 饱和",
            "root": "database",
            "symptom": "Orders DB 写入延迟升高，payment 与 inventory 同时受影响。",
            "risk": {"database": 0.95, "payment": 0.72, "inventory": 0.68, "checkout": 0.56},
            "latency": {"database": 1040, "payment": 620, "inventory": 580, "checkout": 460},
            "error": {"database": 0.041, "payment": 0.027, "inventory": 0.025},
        },
        "service_mesh_config_drift": {
            "name": "服务网格配置漂移",
            "root": "node-b",
            "symptom": "node-b 上的路由配置漂移，payment 调用路径出现间歇性失败。",
            "risk": {"node-b": 0.9, "payment": 0.7, "checkout": 0.55},
            "latency": {"payment": 610, "checkout": 420},
            "error": {"payment": 0.061, "checkout": 0.026},
        },
        "retry_storm": {
            "name": "重试风暴",
            "root": "checkout",
            "symptom": "checkout 对 payment/inventory 重试过多，造成级联放大。",
            "risk": {"checkout": 0.92, "payment": 0.74, "inventory": 0.72, "gateway": 0.58},
            "latency": {"checkout": 910, "payment": 620, "inventory": 590, "gateway": 430},
            "error": {"checkout": 0.067, "payment": 0.035, "inventory": 0.033},
        },
        "deployment_version_skew": {
            "name": "灰度版本不一致",
            "root": "catalog",
            "symptom": "catalog 灰度版本协议不一致，recommendation 与 cart 解析异常。",
            "risk": {"catalog": 0.86, "recommendation": 0.66, "cart": 0.5},
            "latency": {"catalog": 520, "recommendation": 460, "cart": 330},
            "error": {"catalog": 0.049, "recommendation": 0.032, "cart": 0.017},
        },
        "observability_gap": {
            "name": "可观测数据缺口",
            "root": "gateway",
            "symptom": "入口告警存在但部分 trace 缺失，需依赖日志与指标交叉验证。",
            "risk": {"gateway": 0.79, "checkout": 0.57, "catalog": 0.49},
            "latency": {"gateway": 510, "checkout": 390, "catalog": 360},
            "error": {"gateway": 0.029, "checkout": 0.018, "catalog": 0.015},
        },
    }
    profile = profiles.get(scenario, profiles["checkout_latency"])
    base_services = [
        ("gateway", "API Gateway", "edge", -4.8, 0.4, 0.0),
        ("user", "User", "service", -2.8, 1.4, -1.6),
        ("catalog", "Catalog", "service", -1.2, 1.0, 1.6),
        ("recommendation", "Recommendation", "service", 0.7, 1.8, -1.8),
        ("cart", "Cart", "service", 0.8, 0.4, 0.2),
        ("checkout", "Checkout", "service", 2.7, 1.0, 1.4),
        ("payment", "Payment", "service", 4.6, 1.4, -0.6),
        ("inventory", "Inventory", "service", 4.4, -0.4, 1.8),
        ("shipping", "Shipping", "service", 5.8, 0.5, 0.6),
        ("node-a", "Node A", "infra", 1.8, -1.8, -1.4),
        ("node-b", "Node B", "infra", 4.2, -1.8, 1.2),
        ("database", "Orders DB", "data", 6.5, -0.8, -1.2),
    ]
    services = []
    risk_map = profile["risk"]
    for svc_id, name, layer, x, y, z in base_services:
        risk = float(risk_map.get(svc_id, 0.18))
        services.append({
            "id": svc_id,
            "name": name,
            "layer": layer,
            "status": "critical" if risk >= 0.82 else "degraded" if risk >= 0.5 else "healthy",
            "risk": round(risk, 3),
            "position": {"x": x, "y": y, "z": z},
            "metrics": {
                "latency_p95_ms": profile["latency"].get(svc_id, 120 + int(risk * 180)),
                "error_rate": round(profile["error"].get(svc_id, 0.006 + risk * 0.012), 4),
                "cpu": round(0.28 + risk * 0.56, 3),
                "memory": round(0.34 + risk * 0.44, 3),
            },
        })
    edges = [
        ("gateway", "user"), ("gateway", "catalog"), ("gateway", "cart"),
        ("catalog", "recommendation"), ("cart", "checkout"), ("checkout", "payment"),
        ("checkout", "inventory"), ("checkout", "shipping"), ("payment", "database"),
        ("inventory", "database"), ("node-a", "checkout"), ("node-a", "catalog"), ("node-b", "payment"),
    ]
    edge_payload = []
    for src, dst in edges:
        src_risk = risk_map.get(src, 0.18)
        dst_risk = risk_map.get(dst, 0.18)
        edge_payload.append({
            "source": src,
            "target": dst,
            "traffic_rps": 80 + int((src_risk + dst_risk) * 120),
            "risk_flow": round(max(src_risk, dst_risk), 3),
        })
    return {
        "id": "builtin-observability-simulator",
        "name": "内置可观测模拟系统",
        "scenario": scenario,
        "scenario_name": profile["name"],
        "root": profile["root"],
        "symptom": profile["symptom"],
        "status": "degraded",
        "port_hint": 8080,
        "health_path": "/api/guard-sim/health",
        "services": services,
        "edges": edge_payload,
        "slo": {
            "availability": 0.995,
            "latency_p95_ms": max(s["metrics"]["latency_p95_ms"] for s in services if s["layer"] == "service"),
            "error_budget_burn": round(1.2 + max(risk_map.values()) * 4.8, 2),
        },
        "events": [
            {"level": "warn", "service": profile["root"], "message": profile["symptom"]},
            {"level": "info", "service": "gateway", "message": "持续守护已采集入口延迟、错误率和依赖传播。"},
            {"level": "action", "service": "sre-agent", "message": "建议先执行只读 RCA 预热，恢复动作需人工确认。"},
        ],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _guard_target_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    mode = str(body.get("mode") or "real").strip()
    if mode not in {"real", "simulated"}:
        mode = "real"
    scenario = str(body.get("scenario") or "checkout_latency").strip()
    raw_endpoint = str(body.get("endpoint") or "").strip()
    system_path = str(body.get("system_path") or "").strip()
    endpoint = raw_endpoint if mode == "simulated" else _normalize_guard_endpoint(
        raw_endpoint,
        str(body.get("port") or ""),
        str(body.get("health_path") or "/health"),
    )
    system_endpoint = (
        f"/api/guard-sim/system?scenario={scenario}" if mode == "simulated"
        else _normalize_guard_endpoint(raw_endpoint, str(body.get("port") or ""), system_path) if system_path
        else ""
    )
    name = str(body.get("name") or "").strip()
    if mode == "real" and not endpoint:
        raise HTTPException(400, "真实系统接入需要 endpoint 或 host/port")
    scenario_name = _guard_simulated_system(scenario)["scenario_name"] if mode == "simulated" else ""
    target = {
        "id": body.get("id") or f"guard-target-{uuid.uuid4().hex[:8]}",
        "name": name or ("真实系统端口" if mode == "real" else f"内置可观测模拟系统:{scenario_name}"),
        "mode": mode,
        "endpoint": endpoint or (f"/api/guard-sim/health?scenario={scenario}" if mode == "simulated" else ""),
        "system_endpoint": system_endpoint,
        "scenario": scenario,
        "token_configured": bool(str(body.get("token") or "").strip()),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_probe": None,
    }
    return target


def _probe_guard_target(target: Dict[str, Any]) -> Dict[str, Any]:
    if target.get("mode") == "simulated":
        scenario = target.get("scenario") or "checkout_latency"
        system = _guard_simulated_system(scenario)
        probe = {
            "reachable": True,
            "status_code": 200,
            "summary": f"{system['name']}已就绪：{system['scenario_name']}，根因候选 {system['root']}",
            "signals": {
                "latency_p95_ms": system["slo"]["latency_p95_ms"],
                "error_budget_burn": system["slo"]["error_budget_burn"],
                "critical_services": len([s for s in system["services"] if s["status"] == "critical"]),
                "degraded_services": len([s for s in system["services"] if s["status"] == "degraded"]),
            },
            "system": system,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        target["last_probe"] = probe
        return probe

    import urllib.error
    import urllib.request

    url = target.get("endpoint") or ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OpsFactory-Guard/5.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = resp.read(250000).decode("utf-8", errors="replace")
            system_payload = None
            try:
                system_payload = _extract_guard_system_payload(json.loads(body))
            except Exception:
                system_payload = None
            system_summary = "，已加载企业系统沙盘数据" if system_payload else ""
            system_endpoint = target.get("system_endpoint") or ""
            if not system_payload and system_endpoint:
                try:
                    system_req = urllib.request.Request(system_endpoint, headers={"User-Agent": "OpsFactory-Guard/5.0"})
                    with urllib.request.urlopen(system_req, timeout=4) as system_resp:
                        system_body = system_resp.read(250000).decode("utf-8", errors="replace")
                        system_payload = _extract_guard_system_payload(json.loads(system_body))
                        if system_payload:
                            system_summary = "，已加载企业系统沙盘数据"
                except Exception as system_exc:
                    system_summary = f"，但系统沙盘数据读取失败：{system_exc}"
            probe = {
                "reachable": 200 <= int(resp.status) < 500,
                "status_code": int(resp.status),
                "summary": f"真实系统端口可访问，HTTP {resp.status}{system_summary}",
                "sample": body[:300],
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            if system_payload:
                probe["system"] = system_payload
                probe["signals"] = {
                    "latency_p95_ms": (system_payload.get("slo") or {}).get("latency_p95_ms", "-"),
                    "error_budget_burn": (system_payload.get("slo") or {}).get("error_budget_burn", "-"),
                    "critical_services": len([s for s in system_payload.get("services", []) if s.get("status") == "critical"]),
                    "degraded_services": len([s for s in system_payload.get("services", []) if s.get("status") == "degraded"]),
                }
    except urllib.error.HTTPError as exc:
        probe = {
            "reachable": exc.code < 500,
            "status_code": exc.code,
            "summary": f"端口有响应但返回 HTTP {exc.code}",
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    except Exception as exc:
        probe = {
            "reachable": False,
            "status_code": None,
            "summary": f"真实系统端口探测失败：{exc}",
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    target["last_probe"] = probe
    return probe


# ── Kubectl Helpers ──

def _kubectl_sync(cmd: str, namespace: str = "") -> str:
    """Execute kubectl command via SSH jump host (synchronous)."""
    import subprocess
    cfg = _get_config()
    ns_flag = f"-n {namespace}" if namespace else ""

    if cfg.kubernetes.use_ssh and cfg.kubernetes.ssh_jump_host:
        ssh_target = cfg.kubernetes.ssh_target or cfg.kubernetes.target_host
        ssh_cmd = f"ssh -J {cfg.kubernetes.ssh_jump_host} {ssh_target} 'kubectl {cmd} {ns_flag}'"
    else:
        ssh_cmd = f"kubectl {cmd} {ns_flag}"

    try:
        result = subprocess.run(
            ssh_cmd, shell=True, capture_output=True, text=True, timeout=30
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return "Error: command timed out"
    except Exception as e:
        return f"Error: {e}"


async def _kubectl(cmd: str, namespace: str = "") -> str:
    """Execute kubectl command without blocking the event loop."""
    return await asyncio.to_thread(_kubectl_sync, cmd, namespace)


async def _kubectl_json(cmd: str, namespace: str = "") -> Any:
    """Execute kubectl -o json and parse without blocking."""
    raw = await _kubectl(f"{cmd} -o json", namespace)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": raw}


# ─────────────────────────────────────────
# Page Routes
# ─────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


# ─────────────────────────────────────────
# Cluster Info APIs
# ─────────────────────────────────────────

@app.get("/api/cluster/overview")
async def cluster_overview():
    """Cluster health summary."""
    nodes_raw, pods_raw = await asyncio.gather(
        _kubectl_json("get nodes"),
        _kubectl_json("get pods --all-namespaces"),
    )
    
    nodes = nodes_raw.get("items", [])
    pods = pods_raw.get("items", [])
    
    # Count pod phases
    phases = {}
    for pod in pods:
        phase = pod.get("status", {}).get("phase", "Unknown")
        phases[phase] = phases.get(phase, 0) + 1
    
    namespace_names = {
        pod.get("metadata", {}).get("namespace", "")
        for pod in pods
        if pod.get("metadata", {}).get("namespace", "")
    }

    return {
        "nodes": len(nodes),
        "pods_total": len(pods),
        "pod_phases": phases,
        "namespaces": len(namespace_names),
    }


@app.get("/api/cluster/nodes")
async def cluster_nodes():
    """Detailed node info."""
    data = await _kubectl_json("get nodes")
    nodes = []
    for n in data.get("items", []):
        meta = n.get("metadata", {})
        status = n.get("status", {})
        conditions = {c["type"]: c["status"] for c in status.get("conditions", [])}
        nodes.append({
            "name": meta.get("name", ""),
            "roles": [l.split("/")[-1] for l in meta.get("labels", {}) if "node-role" in l],
            "ready": conditions.get("Ready", "Unknown"),
            "version": status.get("nodeInfo", {}).get("kubeletVersion", ""),
            "os": status.get("nodeInfo", {}).get("osImage", ""),
            "cpu": status.get("capacity", {}).get("cpu", ""),
            "memory": status.get("capacity", {}).get("memory", ""),
        })
    return {"nodes": nodes}


@app.get("/api/cluster/namespaces")
async def cluster_namespaces():
    raw = await _kubectl("get namespaces -o jsonpath='{.items[*].metadata.name}'")
    return {"namespaces": raw.replace("'", "").split()}


@app.get("/api/cluster/pods")
async def cluster_pods(namespace: str = ""):
    """List pods with status info."""
    if namespace:
        data = await _kubectl_json("get pods", namespace)
    else:
        data = await _kubectl_json("get pods --all-namespaces")
    pods = []
    for p in data.get("items", []):
        meta = p.get("metadata", {})
        status = p.get("status", {})
        containers = status.get("containerStatuses", [])
        ready = sum(1 for c in containers if c.get("ready"))
        restarts = sum(c.get("restartCount", 0) for c in containers)
        pods.append({
            "name": meta.get("name", ""),
            "namespace": meta.get("namespace", ""),
            "phase": status.get("phase", "Unknown"),
            "ready": f"{ready}/{len(containers)}",
            "restarts": restarts,
            "node": p.get("spec", {}).get("nodeName", ""),
            "age": meta.get("creationTimestamp", ""),
        })
    return {"pods": pods}


@app.get("/api/cluster/events")
async def cluster_events(namespace: str = "", limit: int = 50):
    """Recent K8s events."""
    if namespace:
        data = await _kubectl_json("get events --sort-by=.lastTimestamp", namespace)
    else:
        data = await _kubectl_json("get events --sort-by=.lastTimestamp --all-namespaces")
    events = []
    for e in data.get("items", [])[-limit:]:
        events.append({
            "type": e.get("type", ""),
            "reason": e.get("reason", ""),
            "message": e.get("message", ""),
            "source": e.get("source", {}).get("component", ""),
            "object": e.get("involvedObject", {}).get("name", ""),
            "namespace": e.get("involvedObject", {}).get("namespace", ""),
            "count": e.get("count", 1),
            "last_seen": e.get("lastTimestamp", ""),
        })
    return {"events": events}


@app.get("/api/cluster/services")
async def cluster_services(namespace: str = ""):
    if namespace:
        data = await _kubectl_json("get services", namespace)
    else:
        data = await _kubectl_json("get services --all-namespaces")
    services = []
    for s in data.get("items", []):
        meta = s.get("metadata", {})
        spec = s.get("spec", {})
        ports = [f"{p.get('port')}/{p.get('protocol','TCP')}" for p in spec.get("ports", [])]
        services.append({
            "name": meta.get("name", ""),
            "namespace": meta.get("namespace", ""),
            "type": spec.get("type", ""),
            "cluster_ip": spec.get("clusterIP", ""),
            "ports": ", ".join(ports),
        })
    return {"services": services}


@app.get("/api/logs/{namespace}/{pod}")
async def pod_logs(namespace: str, pod: str, lines: int = 200, container: str = ""):
    c_flag = f"-c {container}" if container else ""
    raw = await _kubectl(f"logs {pod} {c_flag} --tail={lines}", namespace)
    return {"logs": raw}


# ─────────────────────────────────────────
# Prometheus Query APIs
# ─────────────────────────────────────────

def _prom_query_sync(query: str, query_type: str = "instant",
                     start: str = "", end: str = "", step: str = "60s") -> dict:
    """Execute Prometheus query synchronously."""
    cfg = _get_config()
    base_url = cfg.observability.prometheus_url
    if not base_url:
        return {"error": "Prometheus URL not configured"}

    import requests as req
    try:
        if query_type == "range":
            url = f"{base_url}/api/v1/query_range"
            params = {"query": query, "step": step}
            params["start"] = start or str(int(time.time()) - 3600)
            params["end"] = end or str(int(time.time()))
        else:
            url = f"{base_url}/api/v1/query"
            params = {"query": query}

        resp = req.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "success":
            return {"results": data.get("data", {}).get("result", []),
                    "resultType": data.get("data", {}).get("resultType", "")}
        return {"error": data.get("error", "Unknown error")}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/prometheus/query")
async def prometheus_query(query: str = "", query_type: str = "instant",
                           start: str = "", end: str = "", step: str = "60s"):
    """Execute arbitrary PromQL queries."""
    if not query:
        raise HTTPException(400, "Missing 'query' parameter")
    result = await asyncio.to_thread(_prom_query_sync, query, query_type, start, end, step)
    return result


@app.get("/api/prometheus/metrics_summary")
async def prometheus_metrics_summary(namespace: str = ""):
    """Pre-built metrics summary for the dashboard: node CPU/mem/disk + container top."""
    ns_filter = f'namespace="{namespace}"' if namespace else ''

    queries = {
        "node_cpu": 'avg by(instance)(1 - rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100',
        "node_memory": '(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100',
        "node_disk": '(1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) * 100',
    }

    if ns_filter:
        queries["container_cpu_top"] = (
            f'topk(10, sum by(pod)(rate(container_cpu_usage_seconds_total{{{ns_filter}}}[5m])) * 100)'
        )
        queries["container_mem_top"] = (
            f'topk(10, sum by(pod)(container_memory_working_set_bytes{{{ns_filter}}}) / 1024 / 1024)'
        )
    else:
        queries["container_cpu_top"] = (
            'topk(10, sum by(pod, namespace)(rate(container_cpu_usage_seconds_total[5m])) * 100)'
        )
        queries["container_mem_top"] = (
            'topk(10, sum by(pod, namespace)(container_memory_working_set_bytes) / 1024 / 1024)'
        )

    import concurrent.futures
    results = {}

    def _query_one(key, q):
        return key, _prom_query_sync(q)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_query_one, k, q) for k, q in queries.items()]
        for f in concurrent.futures.as_completed(futures):
            k, v = f.result()
            results[k] = v

    return results


# ─────────────────────────────────────────
# Jaeger Trace APIs
# ─────────────────────────────────────────

def _jaeger_request(path: str, params: dict = None) -> dict:
    """Execute Jaeger API request synchronously."""
    cfg = _get_config()
    base_url = cfg.observability.jaeger_url
    if not base_url:
        return {"error": "Jaeger URL not configured"}

    import requests as req
    try:
        url = f"{base_url.rstrip('/')}{path}"
        resp = req.get(url, params=params or {}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/jaeger/services")
async def jaeger_services():
    """List available services in Jaeger."""
    data = await asyncio.to_thread(_jaeger_request, "/api/services")
    services = data.get("data", []) if not data.get("error") else []
    return {"services": services, "error": data.get("error")}


@app.get("/api/jaeger/traces")
async def jaeger_traces(service: str = "", operation: str = "",
                        min_duration: str = "", max_duration: str = "",
                        limit: int = 20, lookback: str = "1h"):
    """Search traces by service and filters."""
    if not service:
        raise HTTPException(400, "Missing 'service' parameter")

    params = {"service": service, "limit": limit, "lookback": lookback}
    if operation:
        params["operation"] = operation
    if min_duration:
        params["minDuration"] = min_duration
    if max_duration:
        params["maxDuration"] = max_duration

    data = await asyncio.to_thread(_jaeger_request, "/api/traces", params)
    if data.get("error"):
        return {"traces": [], "error": data["error"]}

    traces = data.get("data", [])
    summaries = []
    for trace in traces[:limit]:
        spans = trace.get("spans", [])
        services_in_trace = list(set(
            s.get("process", {}).get("serviceName", "") for s in spans
        ))
        durations = [s.get("duration", 0) for s in spans]
        root_span = next((s for s in spans if not s.get("references")), spans[0] if spans else {})
        summaries.append({
            "traceID": trace.get("traceID", ""),
            "root_service": root_span.get("process", {}).get("serviceName", ""),
            "root_operation": root_span.get("operationName", ""),
            "span_count": len(spans),
            "services": services_in_trace,
            "total_duration_us": max(durations) if durations else 0,
            "avg_duration_us": sum(durations) // max(len(durations), 1),
            "start_time": root_span.get("startTime", 0),
        })

    return {"traces": summaries, "total": len(summaries)}


@app.get("/api/jaeger/trace/{trace_id}")
async def jaeger_trace_detail(trace_id: str):
    """Get full trace detail by trace ID."""
    data = await asyncio.to_thread(_jaeger_request, f"/api/traces/{trace_id}")
    if data.get("error"):
        return {"error": data["error"]}

    traces = data.get("data", [])
    if not traces:
        raise HTTPException(404, "Trace not found")

    trace = traces[0]
    spans = trace.get("spans", [])
    processes = trace.get("processes", {})

    span_list = []
    for s in spans:
        pid = s.get("processID", "")
        proc = processes.get(pid, {})
        span_list.append({
            "spanID": s.get("spanID", ""),
            "operationName": s.get("operationName", ""),
            "serviceName": proc.get("serviceName", ""),
            "duration_us": s.get("duration", 0),
            "startTime": s.get("startTime", 0),
            "tags": {t["key"]: t["value"] for t in s.get("tags", [])},
            "logs": [{"ts": l.get("timestamp"), "fields": l.get("fields")} for l in s.get("logs", [])],
            "references": s.get("references", []),
        })

    return {
        "traceID": trace.get("traceID"),
        "spans": span_list,
        "span_count": len(span_list),
        "processes": processes,
    }


@app.get("/api/jaeger/operations")
async def jaeger_operations(service: str = ""):
    """List operations for a Jaeger service."""
    if not service:
        return {"operations": []}
    data = await asyncio.to_thread(_jaeger_request, f"/api/services/{service}/operations")
    operations = data.get("data", []) if not data.get("error") else []
    return {"operations": operations, "error": data.get("error")}


# ─────────────────────────────────────────
# Detection & Alert APIs
# ─────────────────────────────────────────

@app.get("/api/detection/signals")
async def get_detection_signals():
    return {"signals": list(_state["detection_signals"])}


@app.delete("/api/detection/signals")
async def clear_detection_signals():
    _state["detection_signals"].clear()
    return {"status": "cleared"}


@app.get("/api/detection/stream")
async def detection_stream():
    """SSE stream for detection signals."""
    async def event_gen():
        last_count = 0
        while True:
            current = len(_state["detection_signals"])
            if current > last_count:
                new = list(_state["detection_signals"])[last_count:]
                for s in new:
                    yield f"data: {json.dumps(s)}\n\n"
                last_count = current
            else:
                yield f": heartbeat {int(time.time())}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/api/detection/config")
async def get_detection_config():
    """返回当前检测配置"""
    cfg = _get_config()
    det = cfg.detection
    return {
        "sources_enabled": det.sources_enabled,
        "metric_checks": det.metric_checks,
        "critical_event_reasons": det.critical_event_reasons,
        "critical_pod_reasons": det.critical_pod_reasons,
        "default_detect_methods": det.default_detect_methods,
        "default_lookback_m": det.default_lookback_m,
        "default_z_threshold": det.default_z_threshold,
        "default_ewma_span": det.default_ewma_span,
        "categories_enabled": det.categories_enabled,
        "business_services": det.business_services,
        "db_services": det.db_services,
        "thresholds": det.thresholds,
    }


@app.put("/api/detection/config")
async def update_detection_config(request: Request):
    """运行时更新检测配置（内存生效，不写 YAML）"""
    body = await request.json()
    cfg = _get_config()
    det = cfg.detection
    if "sources_enabled" in body:
        det.sources_enabled.update(body["sources_enabled"])
    if "metric_checks" in body:
        det.metric_checks = body["metric_checks"]
    if "critical_event_reasons" in body:
        det.critical_event_reasons = body["critical_event_reasons"]
    if "critical_pod_reasons" in body:
        det.critical_pod_reasons = body["critical_pod_reasons"]
    if "default_detect_methods" in body:
        det.default_detect_methods = body["default_detect_methods"]
    if "default_lookback_m" in body:
        det.default_lookback_m = int(body["default_lookback_m"])
    if "default_z_threshold" in body:
        det.default_z_threshold = float(body["default_z_threshold"])
    if "default_ewma_span" in body:
        det.default_ewma_span = int(body["default_ewma_span"])
    if "categories_enabled" in body and isinstance(body["categories_enabled"], dict):
        det.categories_enabled.update(body["categories_enabled"])
    if "business_services" in body and isinstance(body["business_services"], list):
        det.business_services = body["business_services"]
    if "db_services" in body and isinstance(body["db_services"], list):
        det.db_services = body["db_services"]
    if "thresholds" in body and isinstance(body["thresholds"], dict):
        det.thresholds.update(body["thresholds"])
    # 重建 pipeline 使配置生效
    _state["pipeline"] = None
    return {
        "status": "ok",
        "detection": {
            "sources_enabled": det.sources_enabled,
            "metric_checks": det.metric_checks,
            "critical_event_reasons": det.critical_event_reasons,
            "critical_pod_reasons": det.critical_pod_reasons,
            "default_detect_methods": det.default_detect_methods,
            "default_lookback_m": det.default_lookback_m,
            "default_z_threshold": det.default_z_threshold,
            "default_ewma_span": det.default_ewma_span,
            "categories_enabled": det.categories_enabled,
            "business_services": det.business_services,
            "db_services": det.db_services,
            "thresholds": det.thresholds,
        },
    }


@app.get("/api/alerts/list")
async def alert_list(namespace: str = ""):
    """Fetch all current alerts from all sources with details."""
    cfg = _get_config()
    registry = build_tool_registry(cfg)
    llm = LLMClient(cfg.llm) if _llm_configured(cfg.llm) else None
    agent = DetectionAgent(llm, registry, cfg)
    signals = await asyncio.to_thread(agent.detect, namespace)
    return {
        "alerts": [s.to_dict() for s in signals],
        "total": len(signals),
        "sources": list(set(s.source for s in signals)),
    }


@app.get("/api/alerts/scan")
async def alert_scan(namespace: str = ""):
    """Run alert compression scan (SOW core)."""
    cfg = _get_config()
    llm_status = await asyncio.to_thread(_prepare_llm_for_request_sync, 60)
    if not llm_status.get("available"):
        raise HTTPException(
            503,
            f"模型服务不可用：{llm_status.get('error') or 'unknown error'}"
        )
    llm = LLMClient(cfg.llm)
    registry = build_tool_registry(cfg)
    agent = AlertAgent(llm, registry)

    # Collect raw alerts first, then compress
    raw_alerts = await agent._collect_alerts(namespace)
    result = await agent.compress_and_recommend(alerts=raw_alerts, namespace=namespace)

    # Attach raw alert details for frontend display
    result["raw_alerts"] = [
        {"name": a.name, "severity": a.severity, "source": a.source,
         "timestamp": a.timestamp, "labels": a.labels, "message": a.message}
        for a in raw_alerts
    ]
    return result


# ─────────────────────────────────────────
# RCA APIs
# ─────────────────────────────────────────

@app.post("/api/rca/run")
async def rca_run(request: Request):
    """Trigger an RCA pipeline."""
    body = await request.json()
    query = body.get("query", "")
    namespace = body.get("namespace", "")
    context = body.get("context", "")
    case_ref = body.get("case_ref", "")

    if not query:
        raise HTTPException(400, "Missing 'query' field")

    final_query = query
    if context:
        final_query = f"{query}\n\n{context}"

    llm_status = await asyncio.to_thread(_prepare_llm_for_request_sync, 60)
    if not llm_status.get("available"):
        raise HTTPException(503, f"模型服务不可用：{llm_status.get('error') or 'unknown error'}")

    run_id = f"rca-{uuid.uuid4().hex[:8]}"
    _state["rca_runs"][run_id] = {
        "id": run_id,
        "query": query,
        "case_ref": case_ref,
        "namespace": namespace,
        "status": "running",
        "logs": [],
        "events": [],
        "result": None,
        "started_at": time.time(),
    }

    def log_cb(msg):
        if isinstance(msg, dict):
            _state["rca_runs"][run_id]["events"].append(msg)
        else:
            _state["rca_runs"][run_id]["logs"].append(msg)

    def _run_sync():
        """Run pipeline in a thread to avoid blocking the event loop."""
        try:
            pipeline = _get_pipeline()
            result = asyncio.run(pipeline.run(final_query, namespace, log_cb))
            _state["rca_runs"][run_id]["result"] = result.to_dict()
            _state["rca_runs"][run_id]["status"] = result.status
        except Exception as e:
            logger.error(f"RCA pipeline error: {e}", exc_info=True)
            _state["rca_runs"][run_id]["status"] = "failed"
            _state["rca_runs"][run_id]["result"] = {"error": str(e)}

    # Run in background thread (pipeline does sync LLM calls that block the loop)
    import threading
    t = threading.Thread(target=_run_sync, daemon=True, name=f"rca-{run_id}")
    t.start()
    return {"run_id": run_id}


@app.get("/api/rca/history")
async def rca_history(limit: int = 20):
    runs = sorted(_state["rca_runs"].values(), key=lambda r: r.get("started_at", 0), reverse=True)
    return {"runs": [
        {
            "id": r["id"],
            "query": r["query"],
            "status": r["status"],
            "started_at": r.get("started_at"),
            "duration_s": (r.get("result", {}) or {}).get("duration_s", 0),
        }
        for r in runs[:limit]
    ]}


@app.get("/api/rca/{run_id}")
async def rca_status(run_id: str):
    run = _state["rca_runs"].get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@app.get("/api/rca/{run_id}/diagnostic-report")
async def rca_diagnostic_report(run_id: str):
    """Download the final ops diagnostic document for Hermes or multi-agent RCA."""
    run = _state["rca_runs"].get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    report = _build_diagnostic_pdf(run)
    filename = f"ops-factory-diagnostic-{run_id}.pdf"
    return Response(
        report,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/rca/{run_id}/stream")
async def rca_stream(run_id: str):
    """SSE stream for RCA execution logs."""
    run = _state["rca_runs"].get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    async def event_gen():
        log_idx = 0
        evt_idx = 0
        while True:
            # Send new logs
            logs = run["logs"]
            if log_idx < len(logs):
                for msg in logs[log_idx:]:
                    yield f"data: {json.dumps({'type': 'log', 'msg': msg})}\n\n"
                log_idx = len(logs)

            # Send new structured events
            events = run.get("events", [])
            if evt_idx < len(events):
                for evt in events[evt_idx:]:
                    yield f"data: {json.dumps({'type': 'event', 'data': evt})}\n\n"
                evt_idx = len(events)

            if run["status"] in ("completed", "failed"):
                yield f"data: {json.dumps({'type': 'done', 'status': run['status'], 'result': run.get('result')})}\n\n"
                break

            yield f": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# ─────────────────────────────────────────
# Remediation APIs
# ─────────────────────────────────────────

@app.post("/api/rca/{run_id}/remediation/approve")
async def rca_remediation_approve(run_id: str):
    """Approve and execute the pending remediation plan."""
    run = _state["rca_runs"].get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    result = run.get("result")
    if not result:
        raise HTTPException(400, "RCA not completed yet")

    # Find remediation data — could be nested in pipeline result
    rca_inner = result.get("result", result)
    rem_data = None
    if isinstance(rca_inner, dict):
        # Check evidence.remediation in the inner RCA result
        evidence = rca_inner.get("evidence", {})
        if isinstance(evidence, dict):
            rem_data = evidence.get("remediation")
    # Also check events for remediation plan
    if not rem_data:
        for evt in reversed(run.get("events", [])):
            if evt.get("event") == "remediation":
                rem_data = evt.get("data")
                break

    if not rem_data or rem_data.get("status") != "pending_approval":
        raise HTTPException(400, "No pending remediation plan found")

    plan = rem_data.get("plan", {})
    if not plan.get("actions"):
        raise HTTPException(400, "Remediation plan has no actions")

    # Execute the plan
    cfg = _get_config()
    from tools import build_tool_registry, LLMClient
    from agents import RemediationAgent
    registry = build_tool_registry(cfg, allow_write=True)
    llm = LLMClient(cfg.llm)
    agent = RemediationAgent(llm, registry, cfg)

    # Override to skip approval check this time
    original_require = agent.require_approval
    agent.require_approval = False
    agent.enabled = True

    rca_result = rca_inner if isinstance(rca_inner, dict) else result
    exec_result = await agent.remediate(rca_result, confidence=1.0, approved=True)

    agent.require_approval = original_require

    # Store the execution result
    run["remediation_result"] = exec_result
    run.setdefault("events", []).append({"event": "remediation_executed", "data": exec_result})

    return exec_result


@app.post("/api/rca/{run_id}/remediation/rollback")
async def rca_remediation_rollback(run_id: str):
    """Roll back the last remediation execution."""
    run = _state["rca_runs"].get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    cfg = _get_config()
    from tools import build_tool_registry, LLMClient
    from agents import RemediationAgent
    registry = build_tool_registry(cfg, allow_write=True)
    llm = LLMClient(cfg.llm)
    agent = RemediationAgent(llm, registry, cfg)

    rollback_result = agent.rollback()
    run["remediation_rollback"] = rollback_result
    run.setdefault("events", []).append({"event": "remediation_rollback", "data": rollback_result})

    return rollback_result


@app.get("/api/rca/{run_id}/remediation")
async def rca_remediation_status(run_id: str):
    """Get remediation status for a run."""
    run = _state["rca_runs"].get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    # Find remediation event
    rem_data = None
    for evt in reversed(run.get("events", [])):
        if evt.get("event") in ("remediation", "remediation_executed", "remediation_rollback"):
            rem_data = evt.get("data")
            break

    return {
        "run_id": run_id,
        "remediation": rem_data,
        "execution": run.get("remediation_result"),
        "rollback": run.get("remediation_rollback"),
    }


# ─────────────────────────────────────────
# Pipeline APIs
# ─────────────────────────────────────────

@app.get("/api/pipeline/history")
async def pipeline_history():
    pipeline = _get_pipeline()
    return {"history": pipeline.get_history()}


@app.get("/api/pipeline/stats")
async def pipeline_stats():
    pipeline = _get_pipeline()
    return pipeline.get_stats()


# ─────────────────────────────────────────
# Daemon Management APIs
# ─────────────────────────────────────────

@app.get("/api/daemon/status")
async def daemon_status():
    daemon = _state.get("daemon")
    if daemon and daemon._running:
        return daemon.status()
    return {"running": False}


@app.post("/api/daemon/start")
async def daemon_start(request: Request):
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    
    if _state.get("daemon") and _state["daemon"]._running:
        return {"status": "already_running"}

    cfg = _get_config()

    def _push_signal(signal_obj):
        """Push detection signal to SSE deque for real-time streaming."""
        _state["detection_signals"].append(
            signal_obj.to_dict() if hasattr(signal_obj, "to_dict") else signal_obj
        )

    daemon = Daemon(
        cfg,
        log_callback=lambda msg: _state["daemon_logs"].append(msg),
        signal_callback=_push_signal,
    )
    _state["daemon"] = daemon

    def run():
        asyncio.run(daemon.start())

    t = threading.Thread(target=run, daemon=True, name="sre-daemon")
    t.start()
    _state["daemon_thread"] = t
    return {"status": "started"}


@app.post("/api/daemon/stop")
async def daemon_stop():
    daemon = _state.get("daemon")
    if daemon and daemon._running:
        asyncio.run_coroutine_threadsafe(daemon.stop(), daemon._loop)
        return {"status": "stopping"}
    return {"status": "not_running"}


@app.get("/api/daemon/logs")
async def daemon_logs(limit: int = 100):
    logs = list(_state["daemon_logs"])[-limit:]
    return {"logs": logs}


@app.get("/api/daemon/logs/stream")
async def daemon_log_stream():
    """SSE stream for daemon logs."""
    async def event_gen():
        idx = 0
        while True:
            logs = list(_state["daemon_logs"])
            if idx < len(logs):
                for msg in logs[idx:]:
                    yield f"data: {json.dumps({'type': 'log', 'msg': msg})}\n\n"
                idx = len(logs)
            
            # Status heartbeat
            daemon = _state.get("daemon")
            if daemon and daemon._running:
                yield f"data: {json.dumps({'type': 'status', 'data': daemon.status()})}\n\n"
            
            yield f": heartbeat\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# ─────────────────────────────────────────
# AliData APIs (Alibaba Cloud Logs & Traces)
# ─────────────────────────────────────────

_alidata_state = {
    "downloader": None,
    "log_tool": None,
    "trace_tool": None,
    "metric_tool": None,
}


def _get_alidata_tools():
    """Lazy-init AliData tools."""
    if _alidata_state["log_tool"] is None:
        from tools.alidata_observability import (
            AliDataLogTool, AliDataTraceTool, AliDataMetricTool,
            create_ali_downloader,
        )
        cfg = _get_config()
        env_file = getattr(cfg.observability, "alidata_env_file", ".env")
        downloader = create_ali_downloader(
            env_file,
            offline_mode=getattr(cfg.observability, "offline_mode", False),
            offline_data_dir=getattr(cfg.observability, "offline_data_dir", ""),
            offline_problem_id=getattr(cfg.observability, "offline_problem_id", ""),
            offline_data_type=getattr(cfg.observability, "offline_data_type", "auto"),
        )
        _alidata_state["downloader"] = downloader
        _alidata_state["log_tool"] = AliDataLogTool(downloader)
        _alidata_state["trace_tool"] = AliDataTraceTool(downloader)
        _alidata_state["metric_tool"] = AliDataMetricTool(downloader)
    return _alidata_state["log_tool"], _alidata_state["trace_tool"]


@app.get("/api/alidata/status")
async def alidata_status():
    """Check AliData connectivity."""
    try:
        log_tool, trace_tool = _get_alidata_tools()
        log_ok = await asyncio.to_thread(log_tool.health_check)
        trace_ok = await asyncio.to_thread(trace_tool.health_check)
        return {"connected": log_ok or trace_ok, "log_ok": log_ok, "trace_ok": trace_ok}
    except Exception as e:
        return {"connected": False, "error": str(e)}


@app.get("/api/offline/problems")
async def offline_problems():
    """List available offline problem datasets for the UI selector."""
    cfg = _get_config()
    current_problem_id = getattr(cfg.observability, "offline_problem_id", "")
    current_data_type = getattr(cfg.observability, "offline_data_type", "auto")

    if not _is_offline_mode():
        return {
            "enabled": False,
            "current_problem_id": current_problem_id,
            "offline_data_type": current_data_type,
            "problems": [],
        }

    data_dir = getattr(cfg.observability, "offline_data_dir", "")
    if not data_dir:
        return {
            "enabled": True,
            "current_problem_id": current_problem_id,
            "offline_data_type": current_data_type,
            "problems": [],
            "error": "offline_data_dir is not configured",
        }

    try:
        from tools.alidata_sdk.utils.local_data_loader import get_local_data_loader

        loader = get_local_data_loader(data_dir=data_dir)
        problem_ids = loader.get_available_problems()
        problems = []

        for problem_id in problem_ids:
            summary = loader.get_data_summary(problem_id)
            availability = summary.get("data_availability", {})
            metadata = summary.get("metadata") or {}
            problems.append({
                "problem_id": problem_id,
                "label": f"problem_{problem_id}",
                "selected": problem_id == current_problem_id,
                "has_failure": all(
                    availability.get(name, False)
                    for name in ("failure_logs", "failure_metrics", "failure_traces")
                ),
                "has_baseline": all(
                    availability.get(name, False)
                    for name in ("baseline_logs", "baseline_metrics")
                ),
                "time_range": metadata.get("time_range", ""),
            })

        return {
            "enabled": True,
            "current_problem_id": current_problem_id,
            "offline_data_type": current_data_type,
            "problems": problems,
        }
    except Exception as e:
        logger.warning("Failed to list offline problems: %s", e)
        return {
            "enabled": True,
            "current_problem_id": current_problem_id,
            "offline_data_type": current_data_type,
            "problems": [],
            "error": str(e),
        }


@app.put("/api/offline/problem")
async def update_offline_problem(request: Request):
    """Switch the active offline problem id at runtime."""
    cfg = _get_config()
    if not _is_offline_mode():
        raise HTTPException(400, "Offline mode is not enabled")

    body = await request.json()
    new_problem_id = _normalize_problem_id(body.get("offline_problem_id", ""))
    if not new_problem_id:
        raise HTTPException(400, "Missing 'offline_problem_id'")

    data_dir = getattr(cfg.observability, "offline_data_dir", "")
    if not data_dir:
        raise HTTPException(400, "offline_data_dir is not configured")

    try:
        from tools.alidata_sdk.utils.local_data_loader import get_local_data_loader

        loader = get_local_data_loader(data_dir=data_dir)
        available_problems = set(loader.get_available_problems())
    except Exception as e:
        raise HTTPException(500, f"Failed to read offline datasets: {e}") from e

    if new_problem_id not in available_problems:
        raise HTTPException(404, f"Offline dataset problem_{new_problem_id} not found")

    new_data_type = str(body.get("offline_data_type") or "").strip().lower()
    if new_data_type and new_data_type not in {"auto", "baseline", "failure"}:
        raise HTTPException(400, "offline_data_type must be one of: auto, baseline, failure")

    cfg.observability.offline_problem_id = new_problem_id
    if new_data_type:
        cfg.observability.offline_data_type = new_data_type

    _refresh_runtime_dependencies()
    logger.info(
        "Switched offline dataset to problem_%s/%s",
        cfg.observability.offline_problem_id,
        getattr(cfg.observability, "offline_data_type", "auto"),
    )

    return {
        "status": "ok",
        "offline_problem_id": cfg.observability.offline_problem_id,
        "offline_data_type": getattr(cfg.observability, "offline_data_type", "auto"),
    }


@app.get("/api/alidata/logs")
async def alidata_logs(query: str = "", time_range: str = "1h",
                       level: str = "", size: int = 200, namespace: str = ""):
    """Fetch logs from Alibaba Cloud SLS/ARMS."""
    try:
        log_tool, _ = _get_alidata_tools()
        result = await asyncio.to_thread(
            log_tool._execute, query=query, time_range=time_range,
            level=level, size=size, namespace=namespace
        )
        if result.success:
            return result.data
        return {"error": result.error, "total_hits": 0, "entries": []}
    except Exception as e:
        return {"error": str(e), "total_hits": 0, "entries": []}


@app.get("/api/alidata/services")
async def alidata_services():
    """List available services from AliData trace data."""
    try:
        _, trace_tool = _get_alidata_tools()
        result = await asyncio.to_thread(trace_tool._execute)
        if result.success:
            services = result.data.get("data", [])
            return {"services": services}
        return {"services": [], "error": result.error}
    except Exception as e:
        return {"services": [], "error": str(e)}


@app.get("/api/alidata/traces")
async def alidata_traces(service: str = "", operation: str = "",
                         min_duration: str = "", max_duration: str = "",
                         limit: int = 20, lookback: str = "1h"):
    """Search traces from Alibaba Cloud ARMS."""
    if not service:
        raise HTTPException(400, "Missing 'service' parameter")
    try:
        _, trace_tool = _get_alidata_tools()
        result = await asyncio.to_thread(
            trace_tool._execute, service=service, operation=operation,
            min_duration=min_duration, max_duration=max_duration,
            limit=limit, lookback=lookback
        )
        if result.success:
            return result.data
        return {"traces": [], "error": result.error}
    except Exception as e:
        return {"traces": [], "error": str(e)}


@app.get("/api/alidata/trace/{trace_id}")
async def alidata_trace_detail(trace_id: str):
    """Get full trace detail by trace ID from AliData."""
    try:
        _, trace_tool = _get_alidata_tools()
        result = await asyncio.to_thread(
            trace_tool._execute, trace_id=trace_id
        )
        if result.success:
            return result.data
        return {"error": result.error}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/alidata/metrics")
async def alidata_metrics(query: str = "", namespace: str = "",
                          start: str = "", end: str = ""):
    """Fetch metrics from Alibaba Cloud ARMS.
    Returns k8s pod metrics (CPU/mem) and APM service metrics (request count, latency).
    """
    try:
        _get_alidata_tools()  # ensure init
        metric_tool = _alidata_state["metric_tool"]
        result = await asyncio.to_thread(
            metric_tool._execute, query=query, namespace=namespace,
            start=start, end=end, max_results=0
        )
        if result.success:
            data = result.data
            # Group by service for frontend display
            k8s_by_service = {}
            apm_by_service = {}
            for r in data.get("results", []):
                metric = r.get("metric", {})
                name = metric.get("__name__", "")
                svc = metric.get("service", "")
                pod = metric.get("pod", "")
                val = r.get("value", [0, "0"])
                values = r.get("values", [])

                if pod:
                    # k8s pod metric
                    k8s_by_service.setdefault(svc, {}).setdefault(pod, {})[name] = {
                        "current": float(val[1]) if len(val) > 1 else 0,
                        "values": values[-60:],
                    }
                else:
                    # APM service metric
                    apm_by_service.setdefault(svc, {})[name] = {
                        "current": float(val[1]) if len(val) > 1 else 0,
                        "values": values[-60:],
                    }

            return {
                "k8s_metrics": k8s_by_service,
                "apm_metrics": apm_by_service,
                "total_results": data.get("result_count", 0),
            }
        return {"error": result.error, "k8s_metrics": {}, "apm_metrics": {}}
    except Exception as e:
        return {"error": str(e), "k8s_metrics": {}, "apm_metrics": {}}


# ─────────────────────────────────────────
# Cloud-OpsBench / OpsAug / PromCopilot APIs
# ─────────────────────────────────────────

@app.get("/api/cloudopsbench/summary")
async def cloudopsbench_summary():
    cfg = _get_config()
    adapter = _get_cloudops_adapter()
    summary = adapter.get_platform_summary()
    summary["enabled"] = bool(getattr(cfg.cloudopsbench, "enabled", True))
    summary["selected_case_ref"] = _state.get("cloudops_selected_case") or getattr(
        cfg.cloudopsbench, "default_case_ref", ""
    )
    return summary


@app.get("/api/cloudopsbench/cases")
async def cloudopsbench_cases(
    system: str = "",
    fault_category: str = "",
    search: str = "",
    limit: Optional[int] = Query(default=None),
):
    cfg = _get_config()
    adapter = _get_cloudops_adapter()
    ui_limit = limit if limit is not None else getattr(cfg.cloudopsbench, "ui_case_limit", 200)
    return {
        "cases": adapter.list_cases(
            system=system,
            fault_category=fault_category,
            search=search,
            limit=ui_limit,
        )
    }


@app.get("/api/cloudopsbench/case/{case_ref:path}/opsaug")
async def cloudopsbench_opsaug(case_ref: str):
    return _get_opsaug_adapter().summarize_case(case_ref)


@app.post("/api/cloudopsbench/case/{case_ref:path}/promcopilot")
async def cloudopsbench_promcopilot(case_ref: str, request: Request):
    body = await request.json()
    question = str(body.get("question", "")).strip()
    if not question:
        raise HTTPException(400, "Missing 'question'")
    return _get_promcopilot_adapter().generate_for_case(case_ref, question)


@app.post("/api/cloudopsbench/case/{case_ref:path}/rca_payload")
async def cloudopsbench_rca_payload(case_ref: str):
    adapter = _get_cloudops_adapter()
    detail = adapter.get_case_detail(case_ref)
    context = adapter.build_case_context_text(case_ref)
    return {
        "case_ref": case_ref,
        "query": detail.get("query", ""),
        "namespace": detail.get("namespace", ""),
        "context": context,
        "suggested_prompt": (
            f"{detail.get('query', '')}\n\n"
            f"请结合以下 Cloud-OpsBench 快照上下文做根因分析：\n{context}"
        ),
    }


@app.get("/api/cloudopsbench/case/{case_ref:path}")
async def cloudopsbench_case_detail(case_ref: str):
    detail = _get_cloudops_adapter().get_case_detail(case_ref)
    _state["cloudops_selected_case"] = case_ref
    return detail


@app.post("/api/cloudopsbench/select")
async def cloudopsbench_select_case(request: Request):
    body = await request.json()
    case_ref = str(body.get("case_ref", "")).strip()
    if not case_ref:
        raise HTTPException(400, "Missing 'case_ref'")
    _state["cloudops_selected_case"] = case_ref
    return {"status": "ok", "case_ref": case_ref}


# ─────────────────────────────────────────
# Model Interaction APIs (local Qwen-0.6B by default)
# ─────────────────────────────────────────

_state["chat_sessions"] = {}  # session_id → {"messages": [], "created_at": time}


@app.get("/api/model/info")
async def get_model_info():
    """Get current LLM model configuration."""
    health = await asyncio.to_thread(_llm_health_sync)
    data = _llm_public_config(health)
    data["reachable"] = bool(health.get("ok"))
    data["local_endpoint"] = data["provider"] == LOCAL_MODEL_PROVIDER
    data["cloudopsbench_root"] = str(_resolve_cloudops_root())
    return data


@app.post("/api/model/provider")
async def set_model_provider(request: Request):
    """Select local Qwen or a user-supplied API for the current runtime."""
    body = await request.json()
    llm_cfg = _set_runtime_llm_provider(
        body.get("provider", LOCAL_MODEL_PROVIDER),
        base_url=body.get("base_url", ""),
        model=body.get("model", ""),
        api_key=body.get("api_key", ""),
        temperature=body.get("temperature"),
        max_tokens=body.get("max_tokens"),
    )
    health = await asyncio.to_thread(_llm_health_sync)
    data = _llm_public_config(health)
    data["reachable"] = bool(health.get("ok"))
    data["message"] = "模型来源已切换。"
    return data


@app.post("/api/model/provider/local")
async def reset_model_provider_to_local():
    """Reset model provider to bundled local Qwen-0.6B."""
    _set_runtime_llm_provider(LOCAL_MODEL_PROVIDER)
    health = await asyncio.to_thread(_llm_health_sync)
    data = _llm_public_config(health)
    data["reachable"] = bool(health.get("ok"))
    data["message"] = "已切回本地 Qwen-0.6B。未启动时可点击“启动本地 Qwen”，对话会快速提示而不会长时间卡住。"
    return data


@app.post("/api/model/start_local")
async def start_local_model():
    """Start bundled local Qwen-0.6B server."""
    _set_runtime_llm_provider(LOCAL_MODEL_PROVIDER)
    return await asyncio.to_thread(_ensure_local_model_server_sync, 120)


@app.post("/api/model/chat")
async def model_chat(request: Request):
    """Send a message to the LLM model and get a response."""
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id", "default")
    stream = body.get("stream", False)

    if not message:
        raise HTTPException(400, "Missing 'message' field")

    cfg = _get_config()
    _normalize_llm_config(cfg.llm)
    if getattr(cfg.llm, "provider", LOCAL_MODEL_PROVIDER) == LOCAL_MODEL_PROVIDER:
        health = await asyncio.to_thread(_llm_health_sync)
        llm_status = {
            "available": bool(health.get("ok")),
            "provider": LOCAL_MODEL_PROVIDER,
            "health": health,
            "error": health.get("error") or "",
        }
    else:
        llm_status = await asyncio.to_thread(_prepare_llm_for_request_sync, MODEL_CHAT_READY_WAIT_S)
    if not llm_status.get("available"):
        raise HTTPException(
            503,
            f"模型服务不可用：{llm_status.get('error', 'unknown error')}。为了避免长时间等待，模型交互不会在提问时冷启动本地模型；请先点击“启动本地 Qwen”，或在模型设置中填写你自己的 OpenAI/Anthropic 兼容 API。"
        )

    # Initialize or update session
    if session_id not in _state["chat_sessions"]:
        _state["chat_sessions"][session_id] = {
            "messages": [],
            "created_at": time.time(),
        }

    session = _state["chat_sessions"][session_id]
    session["messages"].append({"role": "user", "content": message})

    system_prompt = {
        "role": "system",
        "content": f"你是 Ops Factory 的智能运维助手，当前模型为 {cfg.llm.model}。请用中文完整回答问题，保证结论、原因、步骤和注意事项不缺失；如果内容较多，请分段展示。不要输出 <think>、思考草稿或内部推理过程。/no_think"
    }
    llm_messages = [system_prompt] + session["messages"][-4:]

    try:
        llm = LLMClient(cfg.llm)

        if stream:
            async def generate():
                try:
                    response_text = await asyncio.to_thread(llm.chat, llm_messages, None, cfg.llm.max_tokens)
                    session["messages"].append({"role": "assistant", "content": response_text})
                    chunk_size = 50
                    for i in range(0, len(response_text), chunk_size):
                        chunk = response_text[i:i+chunk_size]
                        yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
                        await asyncio.sleep(0.01)
                    yield f"data: {json.dumps({'type': 'done', 'session_id': session_id})}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

            return StreamingResponse(generate(), media_type="text/event-stream")
        else:
            response_text = await asyncio.to_thread(llm.chat, llm_messages, None, cfg.llm.max_tokens)
            session["messages"].append({"role": "assistant", "content": response_text})
            return {
                "response": response_text,
                "session_id": session_id,
                "message_count": len(session["messages"]),
            }
    except Exception as e:
        logger.error(f"Model chat error: {e}", exc_info=True)
        raise HTTPException(500, f"模型调用失败: {str(e)}")


@app.get("/api/model/chat/history/{session_id}")
async def get_chat_history(session_id: str):
    """Get chat history for a session."""
    session = _state["chat_sessions"].get(session_id)
    if not session:
        return {"messages": [], "session_id": session_id}
    return {
        "messages": session["messages"],
        "session_id": session_id,
        "created_at": session["created_at"],
    }


@app.delete("/api/model/chat/history/{session_id}")
async def clear_chat_history(session_id: str):
    """Clear chat history for a session."""
    if session_id in _state["chat_sessions"]:
        _state["chat_sessions"][session_id]["messages"] = []
        return {"status": "cleared", "session_id": session_id}
    return {"status": "not_found", "session_id": session_id}


@app.get("/api/model/chat/sessions")
async def list_chat_sessions():
    """List all chat sessions."""
    sessions = []
    for sid, session in _state["chat_sessions"].items():
        sessions.append({
            "session_id": sid,
            "message_count": len(session["messages"]),
            "created_at": session["created_at"],
        })
    return {"sessions": sessions}


# ─────────────────────────────────────────
# Dynamic Data Source APIs
# ─────────────────────────────────────────

@app.get("/api/datasources/list")
async def list_data_sources():
    """List all available data sources (static + dynamic)."""
    return {"sources": list_all_sources()}


@app.get("/api/datasources/by_type")
async def list_sources_by_type_api(source_type: str = "dynamic"):
    """List data sources filtered by type ('static' or 'dynamic')."""
    return {"sources": list_sources_by_type(source_type)}


@app.get("/api/datasources/custom/schema")
async def custom_datasource_schema():
    """Return the enterprise/custom fault-data integration contract."""
    adapter = get_ds_adapter("custom-enterprise")
    schema = adapter.schema() if hasattr(adapter, "schema") else {}
    return {
        "source_id": "custom-enterprise",
        "purpose": "企业内部系统或自定义故障数据可按此结构注册，然后进入统一 LangChain 多 Agent RCA 流程。",
        "schema": schema,
    }


@app.post("/api/datasources/custom/register_case")
async def register_custom_case(request: Request):
    """Register a custom enterprise fault case."""
    body = await request.json()
    adapter = get_ds_adapter("custom-enterprise")
    if not hasattr(adapter, "register_case"):
        raise HTTPException(500, "custom-enterprise adapter does not support registration")
    try:
        return adapter.register_case(body)
    except DataSourceError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"Custom case registration failed: {e}") from e


@app.get("/api/datasources/{source_id}/info")
async def get_datasource_info(source_id: str):
    """Get detailed info about a specific data source."""
    try:
        adapter = get_ds_adapter(source_id)
        return {
            "source_id": source_id,
            "name": adapter.name,
            "source_type": adapter.source_type,
            "description": adapter.description,
            "faults": adapter.list_faults(),
            "health": adapter.health_check(),
            "injection_capability": injection_capability(source_id, adapter.name, adapter.source_type),
        }
    except Exception as e:
        raise HTTPException(500, f"Data source unavailable: {e}") from e


@app.get("/api/datasources/{source_id}/faults")
async def list_platform_faults(source_id: str):
    """List available faults for a specific platform."""
    adapter = get_ds_adapter(source_id)
    return {"source_id": source_id, "faults": adapter.list_faults()}


@app.post("/api/datasources/{source_id}/inject")
async def inject_platform_fault(source_id: str, request: Request):
    """Inject a fault on a dynamic platform."""
    body = await request.json()
    fault_type = str(body.get("fault_type", ""))
    target = str(body.get("target", ""))
    if not fault_type or not target:
        raise HTTPException(400, "Missing 'fault_type' or 'target'")
    try:
        kwargs = dict(body.get("kwargs") or {})
        for key in (
            "scheduled_at",
            "start_time",
            "duration_seconds",
            "observation_window_seconds",
            "pre_window_seconds",
            "collection_interval_seconds",
            "injection_mode",
            "traffic_profile",
        ):
            if key in body and key not in kwargs:
                kwargs[key] = body[key]
        result = inject_fault_on_platform(source_id, fault_type, target, **kwargs)
        result["source_id"] = source_id
        return result
    except DataSourceError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"Fault injection failed: {e}") from e


@app.post("/api/datasources/{source_id}/case/{case_id:path}/restore")
async def restore_platform_fault(source_id: str, case_id: str, request: Request):
    """Restore a previously injected live Kubernetes fault."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        result = restore_fault_on_platform(
            source_id,
            case_id,
            target=str(body.get("target") or ""),
            fault_type=str(body.get("fault_type") or ""),
        )
        result["source_id"] = source_id
        for run in _state["rca_runs"].values():
            if run.get("source_id") == source_id and run.get("case_id") == case_id:
                run["recovery_result"] = result
        return result
    except NotImplementedError as e:
        raise HTTPException(400, str(e)) from e
    except DataSourceError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"Fault restoration failed: {e}") from e


@app.get("/api/datasources/{source_id}/case/{case_id:path}/topology")
async def get_platform_case_topology(source_id: str, case_id: str):
    adapter = get_ds_adapter(source_id)
    detail = adapter.get_case_detail(case_id)
    return _case_topology_payload(detail)


@app.get("/api/datasources/{source_id}/case/{case_id:path}/evidence")
async def get_platform_case_evidence(source_id: str, case_id: str):
    """Return raw log/trace/metric data samples for modality-specific UI panels."""
    adapter = get_ds_adapter(source_id)
    detail = adapter.get_case_detail(case_id)
    return _case_evidence_payload(detail)


@app.get("/api/datasources/{source_id}/case/{case_id:path}")
async def get_platform_case(source_id: str, case_id: str):
    """Get full case detail from any data source."""
    try:
        adapter = get_ds_adapter(source_id)
        return adapter.get_case_detail(case_id)
    except Exception as e:
        raise HTTPException(500, f"Case detail unavailable: {e}") from e


# ─────────────────────────────────────────
# Unified RCA Orchestration API (End-to-End)
# ─────────────────────────────────────────

@app.post("/api/rca/orchestrated")
async def rca_orchestrated(request: Request):
    """Run the full end-to-end RCA pipeline with tool orchestration.

    Body:
        source_id: Data source identifier (cloud-opsbench, online-shopping, sock-shop, train-ticket)
        case_id: Case or fault identifier
        run_tools: Optional list of tools to run (default: all)
        use_llm: Whether to use LLM for RCA (default: true)

    Returns:
        Full pipeline result including ACC@1/3/5/10 evaluation.
    """
    body = await request.json()
    source_id = str(body.get("source_id", "")).strip()
    case_id = str(body.get("case_id", "")).strip()
    run_tools = body.get("run_tools")
    use_llm = body.get("use_llm", True)

    if not source_id or not case_id:
        raise HTTPException(400, "Missing 'source_id' or 'case_id'")

    # Get data source adapter
    try:
        adapter = get_ds_adapter(source_id)
    except Exception as e:
        raise HTTPException(400, f"Unknown data source: {source_id}: {e}")

    cfg = _get_config()
    llm_client = None
    llm_config = None
    if use_llm:
        llm_status = await asyncio.to_thread(_prepare_llm_for_request_sync, 120)
        if llm_status.get("available"):
            llm_client = LLMClient
            llm_config = cfg.llm
    else:
        llm_status = {
            "requested": False,
            "provider": getattr(cfg.llm, "provider", LOCAL_MODEL_PROVIDER),
            "configured": _llm_configured(cfg.llm),
            "base_url": cfg.llm.base_url,
            "model": cfg.llm.model,
            "available": False,
            "attempted_health_check": False,
            "health": None,
            "bootstrap": None,
            "error": "",
        }

    orchestrator = RcaOrchestrator(
        data_source=adapter,
        llm_client=llm_client,
        llm_config=llm_config,
        llm_status=llm_status,
    )

    # Run in thread to avoid blocking event loop
    result = await asyncio.to_thread(
        orchestrator.run_pipeline, case_id, run_tools
    )
    result["llm_status"] = llm_status
    if isinstance(result.get("rca_result"), dict):
        result["rca_result"].setdefault("llm_status", llm_status)

    # Store in run history
    run_id = f"rca-orch-{uuid.uuid4().hex[:8]}"
    _state["rca_runs"][run_id] = {
        "id": run_id,
        "query": f"[Orchestrated] {source_id}/{case_id}",
        "source_id": source_id,
        "case_id": case_id,
        "status": result.get("status", "unknown"),
        "result": result,
        "started_at": time.time(),
    }

    return {
        "run_id": run_id,
        "llm_requested": bool(use_llm),
        "llm_available": bool(llm_client),
        "llm_status": llm_status,
        **result,
    }


@app.get("/api/rca/orchestrated/history")
async def rca_orchestrated_history(limit: int = 20):
    """Get history of orchestrated RCA runs."""
    runs = sorted(
        [r for r in _state["rca_runs"].values() if "orchestrated" in str(r.get("query", "")).lower()],
        key=lambda r: r.get("started_at", 0), reverse=True,
    )
    return {
        "runs": [
            {
                "id": r["id"],
                "source_id": r.get("source_id", ""),
                "case_id": r.get("case_id", ""),
                "status": r.get("status", "unknown"),
                "acc1": (r.get("result", {}) or {}).get("evaluation", {}).get("ACC@1", 0),
                "acc3": (r.get("result", {}) or {}).get("evaluation", {}).get("ACC@3", 0),
                "acc5": (r.get("result", {}) or {}).get("evaluation", {}).get("ACC@5", 0),
                "acc10": (r.get("result", {}) or {}).get("evaluation", {}).get("ACC@10", 0),
                "duration_s": (r.get("result", {}) or {}).get("duration_s", 0),
                "started_at": r.get("started_at"),
            }
            for r in runs[:limit]
        ]
    }


@app.get("/api/evolution/timeline")
async def evolution_timeline(limit: int = 30):
    """Return recent self-evolution records for visual iteration timeline."""
    evolver = SelfEvolution()
    records = evolver._load_records()[-limit:]
    return {
        "records": [
            {
                "case_id": r.case_id,
                "source_id": r.source_id,
                "timestamp": r.timestamp,
                "hit_at_1": r.hit_at_1,
                "mrr": r.mrr,
                "top_candidate": r.top_candidate,
                "ground_truth_service": r.ground_truth_service,
                "tools_used": r.tools_used,
                "duration_s": r.duration_s,
            }
            for r in records
        ]
    }


@app.get("/api/rca/orchestrated/tools")
async def rca_tool_list():
    """Return list of available tools for the RCA pipeline."""
    return {
        "tools": [
            {"name": "OpsAug", "desc": "五种运维模态融合的故障预警、定位和诊断工具"},
            {"name": "DrainMCP", "desc": "日志单模态故障预警、定位与诊断模型"},
            {"name": "KPIFailure", "desc": "指标单模态故障预警、定位与诊断模型"},
            {"name": "DynamicEvolutionarySystem", "desc": "云原生系统动态演化框架（微服务副本/拓扑/数据流调整）"},
            {"name": "OpsKB", "desc": "运维知识库（服务依赖、部署架构、故障处理知识）"},
            {"name": "PromCopilot", "desc": "基于知识图谱的PromQL查询生成工具"},
        ],
    }


# ─────────────────────────────────────────
# SelfEvolution APIs — Continuous Learning
# ─────────────────────────────────────────

@app.get("/api/evolution/insights")
async def evolution_insights():
    """Get accumulated insights from all past RCA runs."""
    evolver = SelfEvolution()
    return evolver.get_insights()


@app.get("/api/evolution/patterns")
async def evolution_patterns():
    """Get proven fault→root_cause diagnostic patterns."""
    evolver = SelfEvolution()
    return {"patterns": evolver.get_success_patterns()}


@app.get("/api/evolution/failures")
async def evolution_failures(limit: int = 5):
    """Get recent failure cases for review."""
    evolver = SelfEvolution()
    return {"failures": evolver.get_recent_failures(limit)}


@app.get("/api/evolution/failure-learning")
async def evolution_failure_learning(limit: int = 8):
    """Return the concrete failure→attribution→patch→replay Harness loop."""
    evolver = SelfEvolution()
    return evolver.build_failure_learning_workflow(limit=limit)


@app.post("/api/evolution/failure-learning/run")
async def evolution_failure_learning_run(request: Request):
    """Generate a failure learning candidate and optionally publish Harness vNext."""
    body = await request.json()
    limit = int(body.get("limit", 8) or 8)
    publish = bool(body.get("publish", False))
    evolver = SelfEvolution()
    return evolver.run_failure_learning_cycle(limit=limit, publish=publish)


@app.get("/api/evolution/agent_profile")
async def evolution_agent_profile():
    """Get the self-evolving RCA agent capability profile."""
    evolver = SelfEvolution()
    return evolver.get_agent_profile()


@app.post("/api/evolution/suggest_tools")
async def evolution_suggest_tools(request: Request):
    """Suggest which tools to run based on past performance."""
    body = await request.json()
    source_id = str(body.get("source_id", ""))
    fault_type_hint = str(body.get("fault_type_hint", ""))
    evolver = SelfEvolution()
    return {"suggested_tools": evolver.suggest_tool_selection(source_id, fault_type_hint)}


# ─────────────────────────────────────────
# LangChain multi-agent RCA APIs — context/memory/prompt/tool-routing runtime
# ─────────────────────────────────────────

@app.get("/api/multiagent/state")
async def multiagent_state():
    agent = LangChainRCAMultiAgent()
    return agent.get_state()


@app.post("/api/multiagent/tools/register")
async def multiagent_register_tool(request: Request):
    body = await request.json()
    agent = LangChainRCAMultiAgent()
    return agent.register_enterprise_tool(body)


@app.get("/api/enterprise-rca/flows")
async def enterprise_rca_flows():
    """List enterprise RCA algorithms/processes registered into the routing pool."""
    return {"flows": _state["enterprise_rca_flows"]}


@app.post("/api/enterprise-rca/register")
async def enterprise_rca_register(request: Request):
    """Register an enterprise RCA algorithm/process and expose it to the router."""
    body = await request.json()
    flow = _enterprise_flow_payload(body)
    flows = _state["enterprise_rca_flows"]
    existing = next((idx for idx, item in enumerate(flows) if item.get("id") == flow["id"]), None)
    if existing is None:
        flows.append(flow)
    else:
        flows[existing] = flow
    agent = LangChainRCAMultiAgent()
    tool_registration = agent.register_enterprise_tool({
        "id": flow["id"],
        "name": f"RCA流程:{flow['name']}",
        "description": flow["description"] or f"企业内部 {flow['algorithm_type']} 根因定位流程",
        "endpoint": flow["endpoint"],
        "input_modalities": flow["input_modalities"],
        "output_contract": flow["output_contract"],
        "trigger_condition": flow["trigger_condition"],
    })
    return {"status": "ok", "flow": flow, "tool_registration": tool_registration}


@app.get("/api/ops-consult/examples")
async def ops_consult_examples():
    return {
        "examples": [
            "这个故障的日志里最异常的服务是什么？",
            "把当前 case 的指标、链路和拓扑证据合成一个排查结论。",
            "如果我要恢复这个故障，哪些信息必须先确认？",
        ]
    }


@app.post("/api/ops-consult/ask")
async def ops_consult_ask(request: Request):
    """Natural-language ops query over the selected fault context."""
    body = await request.json()
    question = str(body.get("question") or "").strip()
    source_id = str(body.get("source_id") or "").strip()
    case_id = str(body.get("case_id") or "").strip()
    if not question:
        raise HTTPException(400, "Missing question")
    case_summary: Dict[str, Any] = {}
    if source_id and case_id:
        try:
            adapter = get_ds_adapter(source_id)
            case_summary = _consult_summary_from_case(adapter.get_case_detail(case_id))
        except Exception as e:
            case_summary = {"error": str(e), "source_id": source_id, "case_id": case_id}
    answer = {
        "summary": "已按日志、指标、链路和拓扑四类证据组织回答。若选择了当前故障 case，结论会优先引用该 case 的证据摘要。",
        "question": question,
        "case_summary": case_summary,
        "recommended_next_steps": [
            "确认高信号指标是否先于下游错误出现。",
            "检查 Top trace/log 服务是否为传播受害者，而不是直接根因。",
            "需要执行恢复前，先查看 RCA 报告和 Kubernetes 恢复校验面板。",
        ],
        "tool_hint": "如果问题涉及原始数据细节，建议进入数据平台的 Log/Trace/Metric 分模态面板或多智能体工具预案。",
    }
    session = {
        "id": f"consult-{uuid.uuid4().hex[:8]}",
        "question": question,
        "source_id": source_id,
        "case_id": case_id,
        "answer": answer,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _state["ops_consult_sessions"].append(session)
    return session


@app.get("/api/continuous-guard/state")
async def continuous_guard_state():
    plans = sorted(_state["guard_plans"].values(), key=lambda p: p.get("created_at", ""), reverse=True)
    targets = sorted(_state["guard_targets"].values(), key=lambda t: t.get("created_at", ""), reverse=True)
    guard_system = None
    for target in targets:
        probe = target.get("last_probe") or {}
        if probe.get("system"):
            guard_system = probe["system"]
            break
    if guard_system is None:
        guard_system = _guard_simulated_system("checkout_latency")
    return {
        "plans": plans[:20],
        "targets": targets[:20],
        "guard_system": guard_system,
        "runtime": {
            "active_plans": len([p for p in plans if p.get("status") == "active"]),
            "draft_plans": len([p for p in plans if p.get("status") == "draft"]),
            "human_confirm_gates": sum(1 for p in plans if p.get("human_confirm_required")),
            "connected_targets": len(targets),
            "reachable_targets": sum(1 for t in targets if (t.get("last_probe") or {}).get("reachable")),
        },
    }


@app.get("/api/guard-sim/health")
async def guard_sim_health(scenario: str = Query("checkout_latency")):
    system = _guard_simulated_system(scenario)
    return {
        "status": system["status"],
        "system": system["name"],
        "scenario": system["scenario"],
        "root": system["root"],
        "slo": system["slo"],
        "critical_services": [s["id"] for s in system["services"] if s["status"] == "critical"],
        "degraded_services": [s["id"] for s in system["services"] if s["status"] == "degraded"],
        "checked_at": system["generated_at"],
    }


@app.get("/api/guard-sim/system")
async def guard_sim_system(scenario: str = Query("checkout_latency")):
    return _guard_simulated_system(scenario)


@app.get("/api/continuous-guard/targets")
async def continuous_guard_targets():
    targets = sorted(_state["guard_targets"].values(), key=lambda t: t.get("created_at", ""), reverse=True)
    return {"targets": targets}


@app.post("/api/continuous-guard/targets/register")
async def continuous_guard_target_register(request: Request):
    body = await request.json()
    target = _guard_target_payload(body)
    probe = _probe_guard_target(target)
    _state["guard_targets"][target["id"]] = target
    return {"status": "ok", "target": target, "probe": probe}


@app.post("/api/continuous-guard/targets/{target_id}/probe")
async def continuous_guard_target_probe(target_id: str):
    target = _state["guard_targets"].get(target_id)
    if not target:
        raise HTTPException(404, "Guard target not found")
    probe = _probe_guard_target(target)
    return {"status": "ok", "target": target, "probe": probe}


@app.post("/api/continuous-guard/plan")
async def continuous_guard_plan(request: Request):
    body = await request.json()
    plan = _make_guard_plan(
        objective=str(body.get("objective") or ""),
        scope=str(body.get("scope") or ""),
        cadence=str(body.get("cadence") or ""),
        risk_level=str(body.get("risk_level") or "medium"),
    )
    return {"status": "ok", "plan": plan}


@app.post("/api/continuous-guard/{plan_id}/run")
async def continuous_guard_run(plan_id: str):
    plan = _state["guard_plans"].get(plan_id)
    if not plan:
        raise HTTPException(404, "Guard plan not found")
    plan["status"] = "active"
    plan["last_action"] = "最近一次守护执行已完成，报告已写入计划。"
    plan["last_run_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    report = {
        "id": f"guard-report-{uuid.uuid4().hex[:8]}",
        "created_at": plan["last_run_at"],
        "summary": "已完成一次持续守护执行：采集健康摘要、生成风险归因、保留人工确认点。",
        "risk": plan.get("risk_level", "medium"),
        "needs_human_confirm": bool(plan.get("human_confirm_required")),
        "next_action": "高风险处置需要人工确认；低风险巡检结果已写入报告。",
    }
    plan.setdefault("reports", []).insert(0, report)
    return {"status": "ok", "plan": plan, "report": report}


@app.post("/api/multiagent/tool-plan")
async def multiagent_tool_plan(request: Request):
    body = await request.json()
    source_id = str(body.get("source_id", "")).strip()
    case_id = str(body.get("case_id", "")).strip()
    requested = body.get("run_tools")
    if not source_id or not case_id:
        raise HTTPException(400, "Missing 'source_id' or 'case_id'")
    try:
        adapter = get_ds_adapter(source_id)
    except Exception as e:
        raise HTTPException(400, f"Unknown data source: {source_id}: {e}")
    detail = adapter.get_case_detail(case_id)
    evolver = SelfEvolution()
    guidance = evolver.get_runtime_guidance(
        adapter.name,
        str(detail.get("case_name") or detail.get("fault_category") or ""),
    )
    agent = LangChainRCAMultiAgent()
    return agent.build_tool_plan_board(
        detail,
        guidance,
        requested=requested,
        available_tools=RcaOrchestrator.TOOLS,
    )


@app.get("/api/harness/skill-hermes")
async def skill_hermes_status():
    """Show the local SkillClaw + Hermes RCA harness adaptation."""
    harness = SkillHermesAIOpsHarness()
    return {
        "status": "ok",
        "repo_status": harness.repo_status(),
        "purpose": "把 SkillClaw 的技能演化和 Hermes 的上下文/记忆/轨迹机制收敛到 AIOps RCA 场景。",
    }


@app.post("/api/hermes-rca/run")
async def hermes_rca_run(request: Request):
    """Run standalone Hermes + SkillClaw RCA for the selected fault case."""
    body = await request.json()
    source_id = str(body.get("source_id", "")).strip()
    case_id = str(body.get("case_id", "")).strip()
    run_tools = body.get("run_tools")
    use_llm = body.get("use_llm", True)
    if not source_id or not case_id:
        raise HTTPException(400, "Missing 'source_id' or 'case_id'")
    try:
        adapter = get_ds_adapter(source_id)
    except Exception as e:
        raise HTTPException(400, f"Unknown data source: {source_id}: {e}") from e

    cfg = _get_config()
    llm_client = None
    llm_config = None
    if use_llm:
        llm_status = await asyncio.to_thread(_prepare_llm_for_request_sync, 120)
        if llm_status.get("available"):
            llm_client = LLMClient
            llm_config = cfg.llm
    else:
        llm_status = {
            "requested": False,
            "provider": getattr(cfg.llm, "provider", LOCAL_MODEL_PROVIDER),
            "configured": _llm_configured(cfg.llm),
            "base_url": cfg.llm.base_url,
            "model": cfg.llm.model,
            "available": False,
            "attempted_health_check": False,
            "health": None,
            "bootstrap": None,
            "error": "",
        }

    runner = HermesSkillClawRCA(
        data_source=adapter,
        llm_client=llm_client,
        llm_config=llm_config,
        llm_status=llm_status,
    )
    result = await asyncio.to_thread(runner.run, case_id, run_tools)
    result["llm_status"] = llm_status
    if isinstance(result.get("rca_result"), dict):
        result["rca_result"].setdefault("llm_status", llm_status)

    run_id = f"hermes-rca-{uuid.uuid4().hex[:8]}"
    _state["rca_runs"][run_id] = {
        "id": run_id,
        "query": f"[Hermes RCA] {source_id}/{case_id}",
        "source_id": source_id,
        "case_id": case_id,
        "status": result.get("status", "unknown"),
        "result": result,
        "started_at": time.time(),
    }
    return {
        "run_id": run_id,
        "llm_requested": bool(use_llm),
        "llm_available": bool(llm_client),
        "llm_status": llm_status,
        **result,
    }


@app.get("/api/fault-collection/config")
async def fault_collection_config():
    collector = FaultDatasetCollector()
    return {
        "platforms": collector.DEFAULT_PLATFORMS,
        "formats": [
            {"id": "alpaca_sft", "name": "SFT / Alpaca", "description": "instruction + input + output + metadata"},
            {"id": "rl", "name": "RL / Preference", "description": "prompt + chosen + rejected + reward_model + trajectory"},
            {"id": "custom", "name": "用户自定义", "description": "按模板保留变量和元数据"},
        ],
        "harness_policy": collector.harness.build_collection_policy(collector.DEFAULT_PLATFORMS, "alpaca_sft"),
    }


@app.post("/api/fault-collection/start")
async def fault_collection_start(request: Request):
    body = await request.json()
    try:
        collector = FaultDatasetCollector()
        return collector.start_collection(body)
    except Exception as e:
        raise HTTPException(500, f"Fault data collection failed: {e}") from e


@app.get("/api/fault-collection/sessions")
async def fault_collection_sessions():
    collector = FaultDatasetCollector()
    return {"sessions": collector.list_sessions()}


@app.get("/api/fault-collection/session/{session_id}")
async def fault_collection_session(session_id: str):
    collector = FaultDatasetCollector()
    try:
        return collector.get_session(session_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"Unknown collection session: {session_id}") from e


# ─────────────────────────────────────────
# Health & Meta
# ─────────────────────────────────────────

@app.get("/api/health")
async def health():
    cfg = _get_config()
    llm_ok = _llm_configured(cfg.llm)
    cloudops_summary = {}
    try:
        cloudops_summary = _get_cloudops_adapter().get_platform_summary()
    except Exception as e:
        cloudops_summary = {"status": "error", "error": str(e)}
    return {
        "status": "ok",
        "timestamp": time.time(),
        "llm_configured": llm_ok,
        "llm_provider": getattr(cfg.llm, "provider", LOCAL_MODEL_PROVIDER),
        "llm_model": cfg.llm.model,
        "llm_user_api": _llm_is_user_api(cfg.llm),
        "observability_backend": getattr(cfg.observability, "backend", "native"),
        "offline_mode": _is_offline_mode(),
        "offline_problem_id": getattr(cfg.observability, "offline_problem_id", ""),
        "offline_data_type": getattr(cfg.observability, "offline_data_type", ""),
        "cloudopsbench": cloudops_summary,
    }


@app.get("/api/config")
async def get_config_info():
    cfg = _get_config()
    return {
        "observability": {
            "backend": getattr(cfg.observability, "backend", "native"),
            "offline_mode": _is_offline_mode(),
            "offline_problem_id": getattr(cfg.observability, "offline_problem_id", ""),
            "offline_data_type": getattr(cfg.observability, "offline_data_type", ""),
        },
        "llm_model": cfg.llm.model,
        "cloudopsbench": {
            "enabled": getattr(cfg.cloudopsbench, "enabled", True),
            "root_dir": str(_resolve_cloudops_root()),
            "default_case_ref": getattr(cfg.cloudopsbench, "default_case_ref", ""),
            "selected_case_ref": _state.get("cloudops_selected_case"),
        },
        "pipeline": {
            "max_iterations": cfg.pipeline.max_evidence_iterations,
            "confidence_threshold": cfg.pipeline.hypothesis_confidence_threshold,
            "enable_correlation": cfg.pipeline.enable_correlation,
            "enable_graph_rca": cfg.pipeline.enable_graph_rca,
            "enable_recovery": cfg.pipeline.enable_recovery,
        },
        "daemon": {
            "poll_interval": cfg.daemon.poll_interval_seconds,
            "dedup_ttl": cfg.daemon.dedup_ttl_seconds,
            "max_concurrent": cfg.daemon.max_concurrent_pipelines,
        },
    }


# ─────────────────────────────────────────
# Startup
# ─────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    logger.info("Ops Factory Dashboard starting...")
    cfg = _get_config()
    logger.info(
        "Model provider configured: provider=%s model=%s base_url=%s user_api=%s",
        getattr(cfg.llm, "provider", LOCAL_MODEL_PROVIDER),
        cfg.llm.model,
        cfg.llm.base_url,
        _llm_is_user_api(cfg.llm),
    )


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=8080)
