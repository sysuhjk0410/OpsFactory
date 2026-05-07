# -*- coding: utf-8 -*-
"""Shared Kubernetes runtime checks for real fault injection."""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


SOURCE_NAMESPACE_ALIASES = {
    "sock-shop": ["OPSFACTORY_SOCK_SHOP_NAMESPACE", "SOCK_SHOP_NAMESPACE"],
    "online-shopping": [
        "OPSFACTORY_ONLINE_SHOPPING_NAMESPACE",
        "ONLINE_SHOPPING_NAMESPACE",
        "ONLINE_SHOP_NAMESPACE",
    ],
    "train-ticket": ["OPSFACTORY_TRAIN_TICKET_NAMESPACE", "TRAIN_TICKET_NAMESPACE"],
}

OPSFACTORY_FAULT_ANNOTATIONS = (
    "opsfactory.ai/fault-type",
    "opsfactory.ai/fault-method",
    "opsfactory.ai/fault-started-at",
)

POD_EXEC_FAULT_TYPES = {
    "high_cpu",
    "memory_leak",
    "high_latency",
    "network_partition",
    "high_error_rate",
}


def kubectl_command() -> str:
    """Return the configured kubectl executable name/path."""

    return os.environ.get("OPSFACTORY_KUBECTL") or os.environ.get("KUBECTL") or "kubectl"


def resolve_kubectl() -> Optional[str]:
    """Resolve kubectl in the current Ops Factory process environment."""

    cmd = kubectl_command()
    if os.path.sep in cmd and os.path.exists(cmd):
        return cmd
    return shutil.which(cmd)


def source_namespace(source_id: str, default: str) -> str:
    """Allow deployments to override per-platform namespaces without code edits."""

    for key in SOURCE_NAMESPACE_ALIASES.get(source_id, []):
        value = os.environ.get(key)
        if value:
            return value
    return default


def namespace_env_keys(source_id: str) -> List[str]:
    return SOURCE_NAMESPACE_ALIASES.get(source_id, [])


def run_kubectl(args: List[str], *, timeout: int = 15, text: bool = True) -> subprocess.CompletedProcess:
    """Run kubectl with the configured executable."""

    return subprocess.run(
        [kubectl_command(), *args],
        capture_output=True,
        text=text,
        timeout=timeout,
    )


def deployment_container_name(namespace: str, deployment: str) -> str:
    """Return the first container name in a deployment."""

    result = run_kubectl(
        ["get", "deployment", deployment, "-n", namespace, "-o", "json"],
        timeout=15,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            [kubectl_command(), "get", "deployment", deployment, "-n", namespace, "-o", "json"],
            output=result.stdout,
            stderr=result.stderr,
        )
    payload = json.loads(result.stdout or "{}")
    containers = (
        payload.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    if not containers:
        raise RuntimeError(f"Deployment {namespace}/{deployment} has no containers")
    return str(containers[0].get("name") or deployment)


def apply_rollout_command_fault(
    *,
    namespace: str,
    deployment: str,
    fault_type: str,
    reason: str,
) -> Dict[str, Any]:
    """Inject a real Kubernetes fault without relying on shell inside the pod.

    Some local/minimal service images, including the preloaded pause image, do
    not contain /bin/sh, tc, iptables, dd, or stress binaries. In that case a
    Deployment-level command fault is still a real cluster-side fault: the
    Deployment template is patched, Kubernetes rolls a new ReplicaSet, and the
    target Pod enters a startup failure state.
    """

    container = deployment_container_name(namespace, deployment)
    marker = str(int(time.time()))
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "opsfactory.ai/fault-type": fault_type,
                        "opsfactory.ai/fault-method": "deployment-command-fault",
                        "opsfactory.ai/fault-started-at": marker,
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": container,
                            "command": [f"/opsfactory-{fault_type}-fault"],
                            "args": [reason[:180] or "Ops Factory real Kubernetes fault injection"],
                        }
                    ]
                },
            }
        }
    }
    result = run_kubectl(
        [
            "patch",
            "deployment",
            deployment,
            "-n",
            namespace,
            "--type=strategic",
            "-p",
            json.dumps(patch, ensure_ascii=False),
        ],
        timeout=30,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            [kubectl_command(), "patch", "deployment", deployment, "-n", namespace],
            output=result.stdout,
            stderr=result.stderr,
        )
    return {
        "method": "deployment_command_fault",
        "namespace": namespace,
        "deployment": deployment,
        "container": container,
        "fault_type": fault_type,
        "reason": reason,
        "patch_stdout": result.stdout.strip(),
    }


def restore_deployment_fault(
    *,
    namespace: str,
    deployment: str,
    replicas: int = 1,
    fault_type: str = "",
) -> Dict[str, Any]:
    """Restore a Deployment after Ops Factory real fault injection.

    The local fallback injector patches command/args on the Deployment
    template. Pod-crash style injection scales the Deployment to zero. Pod-exec
    faults can leave background processes or network rules inside the old pod,
    so restore performs a rollout restart for those fault types and verifies
    Deployment readiness before reporting success.
    """

    actions: List[Dict[str, Any]] = []
    current = run_kubectl(
        ["get", "deployment", deployment, "-n", namespace, "-o", "json"],
        timeout=15,
    )
    if current.returncode != 0:
        raise subprocess.CalledProcessError(
            current.returncode,
            [kubectl_command(), "get", "deployment", deployment, "-n", namespace, "-o", "json"],
            output=current.stdout,
            stderr=current.stderr,
        )

    payload = json.loads(current.stdout or "{}")
    template = payload.get("spec", {}).get("template", {})
    annotations = template.get("metadata", {}).get("annotations", {}) or {}
    containers = template.get("spec", {}).get("containers", []) or []
    patch_ops: List[Dict[str, str]] = []
    if containers:
        first = containers[0]
        if "command" in first:
            patch_ops.append({"op": "remove", "path": "/spec/template/spec/containers/0/command"})
        if "args" in first:
            patch_ops.append({"op": "remove", "path": "/spec/template/spec/containers/0/args"})
    for key in OPSFACTORY_FAULT_ANNOTATIONS:
        if key in annotations:
            patch_ops.append({
                "op": "remove",
                "path": "/spec/template/metadata/annotations/" + key.replace("/", "~1"),
            })

    if patch_ops:
        patched = run_kubectl(
            [
                "patch",
                "deployment",
                deployment,
                "-n",
                namespace,
                "--type=json",
                "-p",
                json.dumps(patch_ops, ensure_ascii=False),
            ],
            timeout=30,
        )
        if patched.returncode != 0:
            raise subprocess.CalledProcessError(
                patched.returncode,
                [kubectl_command(), "patch", "deployment", deployment, "-n", namespace],
                output=patched.stdout,
                stderr=patched.stderr,
            )
        actions.append({"action": "remove_opsfactory_command_fault", "stdout": patched.stdout.strip()})

    scaled = run_kubectl(
        ["scale", "deployment", deployment, f"--replicas={replicas}", "-n", namespace],
        timeout=30,
    )
    if scaled.returncode != 0:
        raise subprocess.CalledProcessError(
            scaled.returncode,
            [kubectl_command(), "scale", "deployment", deployment, f"--replicas={replicas}", "-n", namespace],
            output=scaled.stdout,
            stderr=scaled.stderr,
        )
    actions.append({"action": "scale_deployment", "replicas": replicas, "stdout": scaled.stdout.strip()})

    should_restart = bool(patch_ops) or _fault_needs_rollout_restart(fault_type)
    if should_restart:
        restarted = run_kubectl(
            ["rollout", "restart", f"deployment/{deployment}", "-n", namespace],
            timeout=30,
        )
        actions.append({
            "action": "rollout_restart",
            "fault_type": fault_type,
            "returncode": restarted.returncode,
            "stdout": restarted.stdout.strip(),
            "stderr": restarted.stderr.strip(),
        })
        if restarted.returncode != 0:
            raise subprocess.CalledProcessError(
                restarted.returncode,
                [kubectl_command(), "rollout", "restart", f"deployment/{deployment}", "-n", namespace],
                output=restarted.stdout,
                stderr=restarted.stderr,
            )

    rollout = run_kubectl(
        ["rollout", "status", f"deployment/{deployment}", "-n", namespace, "--timeout=120s"],
        timeout=135,
    )
    actions.append({
        "action": "rollout_status",
        "returncode": rollout.returncode,
        "stdout": rollout.stdout.strip(),
        "stderr": rollout.stderr.strip(),
    })
    if rollout.returncode != 0:
        raise subprocess.CalledProcessError(
            rollout.returncode,
            [kubectl_command(), "rollout", "status", f"deployment/{deployment}", "-n", namespace],
            output=rollout.stdout,
            stderr=rollout.stderr,
        )

    verification = verify_deployment_recovery(
        namespace=namespace,
        deployment=deployment,
        expected_replicas=replicas,
    )
    if not verification.get("verified"):
        actions.append({
            "action": "restore_verification",
            "status": "failed",
            "reason": verification.get("reason", ""),
        })
        return {
            "status": "restored_unverified",
            "actual_cluster_recovery": False,
            "restore_verified": False,
            "namespace": namespace,
            "deployment": deployment,
            "replicas": replicas,
            "fault_type": fault_type,
            "recovery_strategy": _restore_strategy_label(fault_type, should_restart),
            "verification": verification,
            "actions": actions,
            "message": "Kubernetes restore commands completed, but readiness verification did not pass.",
        }

    actions.append({"action": "restore_verification", "status": "passed"})
    return {
        "status": "restored",
        "actual_cluster_recovery": True,
        "restore_verified": True,
        "namespace": namespace,
        "deployment": deployment,
        "replicas": replicas,
        "fault_type": fault_type,
        "recovery_strategy": _restore_strategy_label(fault_type, should_restart),
        "verification": verification,
        "actions": actions,
    }


def _fault_needs_rollout_restart(fault_type: str) -> bool:
    return str(fault_type or "").strip() in POD_EXEC_FAULT_TYPES


def _restore_strategy_label(fault_type: str, restarted: bool) -> str:
    if restarted and _fault_needs_rollout_restart(fault_type):
        return "scale + rollout restart + readiness verification for pod-exec runtime fault"
    if restarted:
        return "cleanup patched deployment template + rollout verification"
    return "scale + rollout status + readiness verification"


def verify_deployment_recovery(
    *,
    namespace: str,
    deployment: str,
    expected_replicas: int = 1,
) -> Dict[str, Any]:
    """Check that Kubernetes really converged after fault recovery."""

    checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    result = run_kubectl(
        ["get", "deployment", deployment, "-n", namespace, "-o", "json"],
        timeout=15,
    )
    if result.returncode != 0:
        return {
            "verified": False,
            "checked_at": checked_at,
            "reason": _completed_text(result) or "failed to read deployment after restore",
            "namespace": namespace,
            "deployment": deployment,
        }

    payload = json.loads(result.stdout or "{}")
    spec = payload.get("spec", {}) or {}
    status = payload.get("status", {}) or {}
    metadata = payload.get("metadata", {}) or {}
    template = spec.get("template", {}) or {}
    template_meta = template.get("metadata", {}) or {}
    template_spec = template.get("spec", {}) or {}
    containers = template_spec.get("containers", []) or []
    annotations = template_meta.get("annotations", {}) or {}
    ops_annotations = {k: v for k, v in annotations.items() if k.startswith("opsfactory.ai/")}
    command_fault_present = any(
        ("command" in c or "args" in c)
        and str((c.get("command") or c.get("args") or "")).find("opsfactory") >= 0
        for c in containers
        if isinstance(c, dict)
    )
    available_condition = next(
        (c for c in status.get("conditions", []) if c.get("type") == "Available"),
        {},
    )
    generation = int(metadata.get("generation") or 0)
    observed_generation = int(status.get("observedGeneration") or 0)
    desired = int(spec.get("replicas") or 0)
    ready = int(status.get("readyReplicas") or 0)
    available = int(status.get("availableReplicas") or 0)
    updated = int(status.get("updatedReplicas") or 0)
    unavailable = int(status.get("unavailableReplicas") or 0)

    checks = {
        "desired_replicas_match": desired == expected_replicas,
        "ready_replicas_match": ready >= expected_replicas,
        "available_replicas_match": available >= expected_replicas,
        "updated_replicas_match": updated >= expected_replicas,
        "no_unavailable_replicas": unavailable == 0,
        "observed_latest_generation": observed_generation >= generation,
        "available_condition_true": available_condition.get("status") == "True",
        "opsfactory_annotations_cleared": not ops_annotations,
        "opsfactory_command_fault_cleared": not command_fault_present,
    }
    verified = all(checks.values())
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "verified": verified,
        "checked_at": checked_at,
        "reason": "all restore checks passed" if verified else "failed checks: " + ", ".join(failed),
        "namespace": namespace,
        "deployment": deployment,
        "expected_replicas": expected_replicas,
        "replicas": {
            "desired": desired,
            "ready": ready,
            "available": available,
            "updated": updated,
            "unavailable": unavailable,
        },
        "generation": {
            "metadata": generation,
            "observed": observed_generation,
        },
        "available_condition": available_condition,
        "opsfactory_annotations": ops_annotations,
        "checks": checks,
    }


def exec_or_rollout_fault(
    *,
    namespace: str,
    deployment: str,
    pod: str,
    fault_type: str,
    command: List[str],
    fallback_reason: str,
) -> Dict[str, Any]:
    """Try pod exec first; fall back to a real Deployment rollout fault."""

    result = run_kubectl(["exec", pod, "-n", namespace, "--", *command], timeout=30)
    if result.returncode == 0:
        return {
            "method": "pod_exec",
            "namespace": namespace,
            "deployment": deployment,
            "pod": pod,
            "fault_type": fault_type,
            "stdout": result.stdout.strip(),
        }
    reason = (
        fallback_reason
        + "；容器内命令不可用或执行失败，已退到 Kubernetes Deployment 级真实故障。"
        + f" exec_error={_completed_text(result)[:240]}"
    )
    return apply_rollout_command_fault(
        namespace=namespace,
        deployment=deployment,
        fault_type=fault_type,
        reason=reason,
    )


def _completed_text(result: subprocess.CompletedProcess) -> str:
    return (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()


def cluster_health(
    *,
    source_id: str,
    source_name: str,
    namespace: str,
) -> Dict[str, Any]:
    """Return an explainable health contract for real Kubernetes fault injection."""

    ns = source_namespace(source_id, namespace)
    kubeconfig = os.environ.get("KUBECONFIG") or "~/.kube/config"
    base = {
        "source_id": source_id,
        "source_name": source_name,
        "actual_cluster_injection": False,
        "kubectl_command": kubectl_command(),
        "kubectl_path": resolve_kubectl(),
        "kubeconfig": kubeconfig,
        "namespace": ns,
        "namespace_env_keys": namespace_env_keys(source_id),
        "expected_runtime": "Ops Factory 必须能在当前进程环境中执行 kubectl，并访问目标命名空间，才允许真实故障注入。",
    }

    if not base["kubectl_path"]:
        return {
            **base,
            "status": "needs_config",
            "health_state": "kubectl_missing",
            "message": "当前运行 Ops Factory 的环境找不到 kubectl，所以三个动态平台会同时显示不可用。",
            "detail": f"PATH 中未解析到 {kubectl_command()}。这不是某个平台故障，而是共享 Kubernetes 运行时未接入。",
            "action_items": [
                "安装 kubectl，或通过 OPSFACTORY_KUBECTL 指向 kubectl 的绝对路径。",
                "确认启动 uvicorn 的同一个 shell 能执行 kubectl cluster-info。",
                "设置 KUBECONFIG 指向可访问目标集群的 kubeconfig。",
                f"确认命名空间 {ns} 已部署 {source_name}。",
            ],
        }

    configured_kubeconfig = os.environ.get("KUBECONFIG")
    default_kubeconfig = Path.home() / ".kube" / "config"
    if not configured_kubeconfig and not default_kubeconfig.exists():
        return {
            **base,
            "status": "needs_config",
            "health_state": "kubeconfig_missing",
            "message": "kubectl 已安装，但当前环境没有 kubeconfig，因此还没有接入任何真实 Kubernetes 集群。",
            "detail": "未发现 KUBECONFIG 环境变量，也未发现 ~/.kube/config。kubectl 会默认尝试 localhost:8080，这通常不是 Kubernetes API Server。",
            "action_items": [
                "把企业/实验集群 kubeconfig 放到 ~/.kube/config，或设置 KUBECONFIG 指向 kubeconfig 文件。",
                "执行 kubectl config current-context 确认上下文。",
                f"确认命名空间 {ns} 已部署 {source_name}。",
            ],
        }

    try:
        context = run_kubectl(["config", "current-context"], timeout=8)
        current_context = context.stdout.strip() if context.returncode == 0 else ""
        cluster = run_kubectl(["cluster-info"], timeout=10)
        if cluster.returncode != 0:
            return {
                **base,
                "status": "needs_config",
                "health_state": "cluster_unreachable",
                "current_context": current_context,
                "message": "kubectl 已存在，但当前 kubeconfig 无法访问 Kubernetes 控制面。",
                "detail": _completed_text(cluster) or "kubectl cluster-info returned non-zero",
                "action_items": [
                    "检查 KUBECONFIG 是否指向正确集群。",
                    "执行 kubectl config current-context 确认当前上下文。",
                    "确认 VPN/内网和集群证书权限可用。",
                ],
            }

        ns_check = run_kubectl(["get", "namespace", ns], timeout=10)
        if ns_check.returncode != 0:
            return {
                **base,
                "status": "needs_config",
                "health_state": "namespace_missing",
                "current_context": current_context,
                "message": f"Kubernetes 可访问，但命名空间 {ns} 不存在或当前账号无权限。",
                "detail": _completed_text(ns_check),
                "action_items": [
                    f"确认 {source_name} 实际部署命名空间；必要时设置 {', '.join(namespace_env_keys(source_id))}。",
                    f"执行 kubectl get namespace {ns} 验证权限。",
                ],
            }

        pods = run_kubectl(["get", "pods", "-n", ns, "--no-headers"], timeout=10)
        if pods.returncode != 0:
            return {
                **base,
                "status": "needs_config",
                "health_state": "pods_unreadable",
                "current_context": current_context,
                "message": f"命名空间 {ns} 存在，但无法读取 Pod，真实注入前需要确认 RBAC 权限。",
                "detail": _completed_text(pods),
                "action_items": [
                    f"执行 kubectl get pods -n {ns} 验证权限。",
                    "确认当前账号允许 scale deployment、exec pod 或 patch 资源。",
                ],
            }

        pod_lines = [line for line in pods.stdout.splitlines() if line.strip()]
        if not pod_lines:
            return {
                **base,
                "status": "needs_config",
                "health_state": "namespace_empty",
                "current_context": current_context,
                "message": f"命名空间 {ns} 可访问，但未发现 Pod；真实故障注入没有目标。",
                "detail": "",
                "action_items": [
                    f"请先部署 {source_name} 到命名空间 {ns}。",
                    "部署完成后点击重新检测。",
                ],
            }

        return {
            **base,
            "status": "healthy",
            "health_state": "ready",
            "actual_cluster_injection": True,
            "current_context": current_context,
            "message": f"Kubernetes 控制面、命名空间 {ns} 和 Pod 读取均可用，可执行真实故障注入。",
            "detail": f"detected_pod_count={len(pod_lines)}",
            "action_items": [],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            **base,
            "status": "needs_config",
            "health_state": "kubectl_timeout",
            "message": "kubectl 命令超时，当前集群连接不稳定。",
            "detail": str(exc),
            "action_items": ["检查网络/VPN、API Server 连通性和 kubeconfig。"],
        }
    except Exception as exc:
        return {
            **base,
            "status": "error",
            "health_state": "health_check_error",
            "message": "Kubernetes 运行时检测异常。",
            "detail": str(exc),
            "action_items": ["查看后端日志并确认 kubectl/kubeconfig 配置。"],
        }
