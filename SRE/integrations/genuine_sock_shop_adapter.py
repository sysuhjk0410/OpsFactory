# -*- coding: utf-8 -*-
"""Genuine failure fault injection - faults that propagate across services.

Key concept: when a DOWNSTREAM service fails (e.g., database), ALL upstream
services that depend on it also show errors. This creates genuinely ambiguous
evidence where the RCA tools + LLM can genuinely fail.
"""

from __future__ import annotations

import json
import random
import subprocess
import time
from typing import Any, Dict, List, Optional

from .base_data_source import BaseDataSource, DataSourceError
from .fault_injection_schedule import build_fault_injection_window, finalize_fault_injection_window
from .kubernetes_runtime import (
    cluster_health,
    exec_or_rollout_fault,
    kubectl_command,
    restore_deployment_fault,
    run_kubectl,
    source_namespace,
)


class GenuineSockShopAdapter(BaseDataSource):
    """Sock-Shop adapter with genuine fault propagation for honest RCA evaluation."""

    # Standard faults (local to target - should be easy to detect)
    STANDARD_FAULTS = {
        "pod_crash": {"name": "Pod Crash", "severity": "critical", "propagation": "none"},
        "high_cpu": {"name": "High CPU", "severity": "warning", "propagation": "none"},
        "high_latency": {"name": "High Latency", "severity": "warning", "propagation": "none"},
        "memory_leak": {"name": "Memory Leak", "severity": "critical", "propagation": "none"},
    }

    # Propagating faults (downstream failure causes upstream symptoms)
    PROPAGATING_FAULTS = {
        "database_failure": {
            "name": "Database Failure (传播型)",
            "severity": "critical",
            "propagation": "upstream",
            "propagation_deps": ["carts", "orders", "user", "catalogue"],  # services that call the DB
            "description": "MongoDB故障会导致所有调用方(carts/orders/user/catalogue)同时出现连接错误",
        },
        "network_partition": {
            "name": "Network Partition (传播型)",
            "severity": "critical",
            "propagation": "bidirectional",
            "propagation_deps": ["orders", "payment", "shipping"],
            "description": "网络分区导致orders和下游服务(payment/shipping)断开，双方都报错",
        },
        "message_queue_failure": {
            "name": "Message Queue Failure (传播型)",
            "severity": "critical",
            "propagation": "upstream",
            "propagation_deps": ["orders", "payment", "shipping", "queue-master"],
            "description": "RabbitMQ故障导致所有消息消费者(orders/payment/shipping)报错，根因难以定位",
        },
    }

    ALL_FAULTS = {**STANDARD_FAULTS, **PROPAGATING_FAULTS}

    @property
    def name(self): return "Sock-Shop (Genuine)"

    @property
    def source_type(self): return "dynamic"

    @property
    def description(self): return "带真实故障传播的Sock-Shop平台，用于诚实评估RCA能力"

    NAMESPACE = "sock-shop"
    SERVICES = [
        "front-end", "carts", "catalogue", "orders", "payment",
        "shipping", "user", "queue-master", "rabbitmq", "mongodb", "mysql", "session-db",
    ]
    SERVICE_EDGES = [
        {"source": "front-end", "target": "catalogue", "call_type": "http"},
        {"source": "front-end", "target": "carts", "call_type": "http"},
        {"source": "front-end", "target": "orders", "call_type": "http"},
        {"source": "front-end", "target": "user", "call_type": "http"},
        {"source": "front-end", "target": "shipping", "call_type": "http"},
        {"source": "carts", "target": "mongodb", "call_type": "tcp"},
        {"source": "carts", "target": "session-db", "call_type": "tcp"},
        {"source": "orders", "target": "mongodb", "call_type": "tcp"},
        {"source": "orders", "target": "rabbitmq", "call_type": "amqp"},
        {"source": "orders", "target": "shipping", "call_type": "http"},
        {"source": "orders", "target": "payment", "call_type": "http"},
        {"source": "payment", "target": "rabbitmq", "call_type": "amqp"},
        {"source": "shipping", "target": "rabbitmq", "call_type": "amqp"},
        {"source": "queue-master", "target": "rabbitmq", "call_type": "amqp"},
        {"source": "user", "target": "mongodb", "call_type": "tcp"},
        {"source": "catalogue", "target": "mysql", "call_type": "tcp"},
    ]

    _active_faults: Dict[str, Dict[str, Any]] = {}

    def list_faults(self) -> List[Dict[str, Any]]:
        return [
            {"case_id": f"fault-{ftype}", "fault_type": ftype,
             "case_name": f"{fcfg['name']}{' ⚠️传播型' if fcfg.get('propagation') != 'none' else ''}",
             "description": fcfg.get("description", fcfg["name"]),
             "severity": fcfg["severity"],
             "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
            for ftype, fcfg in self.ALL_FAULTS.items()
        ]

    def inject_fault(self, fault_type: str, target: str, **kwargs) -> Dict[str, Any]:
        clean = fault_type.replace("fault-", "", 1) if fault_type.startswith("fault-") else fault_type
        if clean not in self.ALL_FAULTS:
            raise DataSourceError(f"Unknown: {fault_type}")
        if target not in self.SERVICES:
            raise DataSourceError(f"Unknown: {target}")
        case_id = f"gs-{clean}-{target}-{int(time.time() * 1000)}"
        fcfg = self.ALL_FAULTS[clean]
        injection = build_fault_injection_window(
            source_id="sock-shop",
            fault_type=clean,
            target=target,
            kwargs=kwargs,
            default_mode="live_kubernetes_required",
        )
        action = {}
        try:
            action = self._apply_fault_kubectl(clean, target, **kwargs)
            status = "injected"
            deployment = action.get("deployment") or target
            message = (
                f"Fault {clean} injected into {deployment} via kubectl "
                f"(requested_target={target}, propagation={fcfg.get('propagation','none')})"
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError, DataSourceError) as e:
            finalize_fault_injection_window(
                injection,
                status="failed",
                message=f"真实故障注入失败：Sock-Shop 无法访问 Kubernetes 集群或目标服务 {target}: {e}",
            )
            raise DataSourceError(
                f"真实故障注入失败：Sock-Shop 无法访问 Kubernetes 集群、命名空间 {self.NAMESPACE} 或目标服务 {target}。"
                f" 原因: {e}. 未生成仿真 case。"
            ) from e
        injection = finalize_fault_injection_window(injection, status=status, message=message)
        self._active_faults[case_id] = {
            "fault_type": clean, "target": target,
            "severity": fcfg["severity"], "status": status,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fault_injection": injection,
            "fault_action": action,
            "affected_deployment": action.get("deployment") or target,
            "propagation": fcfg.get("propagation", "none"),
            "propagation_deps": fcfg.get("propagation_deps", []),
            "ground_truth": f"{action.get('deployment') or target} is the root cause — {fcfg['name']} fault.",
        }
        return {"case_id": case_id, "status": status, "message": message, "fault_injection": injection}

    def restore_fault(self, case_id: str = "", target: str = "", fault_type: str = "") -> Dict[str, Any]:
        fault = self._active_faults.get(case_id or "")
        clean_type = fault_type or (fault or {}).get("fault_type") or self._parse_case_fault_type(case_id)
        service = target or (fault or {}).get("target") or self._parse_case_target(case_id, clean_type)
        if not service:
            raise DataSourceError("Cannot restore Sock-Shop fault without a target service.")
        deployment = (fault or {}).get("affected_deployment") or self._resolve_fault_deployment(clean_type, service)
        result = restore_deployment_fault(
            namespace=source_namespace("sock-shop", self.NAMESPACE),
            deployment=deployment,
            replicas=1,
            fault_type=clean_type,
        )
        if fault is not None:
            fault["status"] = "restored"
            fault["restored_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        result.update({
            "source_id": "sock-shop",
            "case_id": case_id,
            "target": service,
            "restored_deployment": deployment,
            "fault_type": clean_type,
            "message": f"Sock-Shop fault on {deployment} has been restored.",
        })
        return result

    def _parse_case_target(self, case_id: str, fault_type: str = "") -> str:
        value = str(case_id or "")
        candidates = [fault_type] if fault_type else list(self.ALL_FAULTS.keys())
        for ftype in candidates:
            prefix = f"gs-{ftype}-"
            if value.startswith(prefix):
                rest = value[len(prefix):]
                return rest.rsplit("-", 1)[0] if "-" in rest else rest
        return ""

    def _parse_case_fault_type(self, case_id: str) -> str:
        value = str(case_id or "")
        for ftype in self.ALL_FAULTS.keys():
            if value.startswith(f"gs-{ftype}-"):
                return ftype
        return ""

    def _apply_fault_kubectl(self, fault_type: str, target: str, **kwargs) -> Dict[str, Any]:
        """Execute the real Kubernetes-side fault action for Sock-Shop."""

        ns = source_namespace("sock-shop", self.NAMESPACE)
        deployment = self._resolve_fault_deployment(fault_type, target)
        if fault_type in {"pod_crash", "database_failure", "message_queue_failure"}:
            result = run_kubectl(
                ["scale", "deployment", deployment, "--replicas=0", "-n", ns],
                timeout=30,
            )
            if result.returncode != 0:
                raise subprocess.CalledProcessError(
                    result.returncode,
                    [kubectl_command(), "scale", "deployment", deployment, "--replicas=0", "-n", ns],
                    output=result.stdout,
                    stderr=result.stderr,
                )
            return {
                "method": "scale_deployment_zero",
                "namespace": ns,
                "deployment": deployment,
                "requested_target": target,
                "stdout": result.stdout.strip(),
            }

        pod = self._get_pod_name(target)
        if fault_type == "high_latency":
            delay_ms = str(kwargs.get("delay_ms", 200)).removesuffix("ms")
            return exec_or_rollout_fault(
                namespace=ns,
                deployment=deployment,
                pod=pod,
                fault_type=fault_type,
                command=["tc", "qdisc", "add", "dev", "eth0", "root", "netem", "delay", f"{delay_ms}ms"],
                fallback_reason="高延迟故障需要容器内 tc/netem 能力",
            )
        elif fault_type == "high_cpu":
            return exec_or_rollout_fault(
                namespace=ns,
                deployment=deployment,
                pod=pod,
                fault_type=fault_type,
                command=["sh", "-c", "yes > /dev/null &"],
                fallback_reason="高 CPU 故障需要容器内 shell/yes 能力",
            )
        elif fault_type == "memory_leak":
            mb = str(kwargs.get("mb", 256))
            return exec_or_rollout_fault(
                namespace=ns,
                deployment=deployment,
                pod=pod,
                fault_type=fault_type,
                command=["sh", "-c", f"dd if=/dev/zero of=/dev/shm/memleak bs=1M count={mb}"],
                fallback_reason="内存泄漏故障需要容器内 shell/dd 能力",
            )
        elif fault_type == "network_partition":
            blocked = str(kwargs.get("block_target") or "payment")
            return exec_or_rollout_fault(
                namespace=ns,
                deployment=deployment,
                pod=pod,
                fault_type=fault_type,
                command=["iptables", "-A", "OUTPUT", "-p", "tcp", "-d", blocked, "-j", "DROP"],
                fallback_reason="网络分区故障需要容器内 iptables 能力",
            )
        raise DataSourceError(f"Unsupported Sock-Shop fault type: {fault_type}")

    def _resolve_fault_deployment(self, fault_type: str, target: str) -> str:
        """Map requested business target to the real Deployment that carries the fault."""

        if fault_type == "database_failure":
            if target == "catalogue":
                return "mysql"
            if target in {"mysql", "mongodb", "session-db"}:
                return target
            return "mongodb"
        if fault_type == "message_queue_failure":
            return "rabbitmq"
        return target

    def _get_pod_name(self, service: str) -> str:
        """Find a Ready pod for a Sock-Shop service using common labels."""

        ns = source_namespace("sock-shop", self.NAMESPACE)
        selectors = [f"name={service}", f"app={service}", f"service={service}"]
        errors: List[str] = []
        for selector in selectors:
            result = run_kubectl(["get", "pods", "-n", ns, "-l", selector, "-o", "json"], timeout=15)
            if result.returncode != 0:
                errors.append(f"{selector}: {(result.stderr or result.stdout or '').strip()}")
                continue
            try:
                payload = json.loads(result.stdout or "{}")
            except json.JSONDecodeError as exc:
                errors.append(f"{selector}: invalid kubectl JSON: {exc}")
                continue
            items = payload.get("items", []) or []
            ready = [pod for pod in items if self._pod_is_ready(pod)]
            if ready:
                return str(ready[0].get("metadata", {}).get("name") or "")
            if items:
                names = ", ".join(str(p.get("metadata", {}).get("name") or "-") for p in items[:4])
                errors.append(f"{selector}: found pods but none Ready: {names}")
            else:
                errors.append(f"{selector}: no pods")

        state = self._deployment_state(ns, service)
        if state:
            replicas = state.get("replicas", 0)
            available = state.get("available", 0)
            if replicas == 0:
                raise DataSourceError(
                    f"Sock-Shop target {ns}/{service} 当前是 0 副本，通常是上一次真实故障注入后没有恢复。"
                    f"请先执行故障恢复，或运行：{kubectl_command()} scale deployment {service} --replicas=1 -n {ns}。"
                )
            raise DataSourceError(
                f"Sock-Shop target {ns}/{service} 目前没有 Ready Pod "
                f"(replicas={replicas}, available={available})；请等待 rollout 完成后重试。"
            )
        raise DataSourceError(
            f"Cannot find Ready pod for Sock-Shop service {service} in namespace {ns}. "
            f"selectors tried: {'; '.join(errors)}"
        )

    def _deployment_state(self, namespace: str, deployment: str) -> Optional[Dict[str, int]]:
        result = run_kubectl(["get", "deployment", deployment, "-n", namespace, "-o", "json"], timeout=15)
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout or "{}")
        return {
            "replicas": int(payload.get("spec", {}).get("replicas") or 0),
            "ready": int(payload.get("status", {}).get("readyReplicas") or 0),
            "available": int(payload.get("status", {}).get("availableReplicas") or 0),
        }

    @staticmethod
    def _pod_is_ready(pod: Dict[str, Any]) -> bool:
        if pod.get("status", {}).get("phase") != "Running":
            return False
        conditions = pod.get("status", {}).get("conditions", []) or []
        return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)

    def get_case_detail(self, case_id: str) -> Dict[str, Any]:
        fault = self._active_faults.get(case_id)
        if not fault:
            return self._detect_anomaly(case_id)
        target = fault["target"]
        fault_type = fault["fault_type"]
        propagation = fault.get("propagation", "none")
        prop_deps = fault.get("propagation_deps", [])
        fcfg = self.ALL_FAULTS.get(fault_type, {})

        return {
            "case_id": case_id,
            "case_name": f"{fcfg.get('name', fault_type)} on {target}",
            "source": self.name, "source_type": self.source_type,
            "timestamp": fault["timestamp"], "severity": fault["severity"],
            "fault_injection": fault.get("fault_injection", {}),
            "root_cause_ground_truth": f"{target} is the root cause — {fcfg.get('name', fault_type)} fault.",
            "metrics": self._gen_metrics(target, fault_type, propagation, prop_deps),
            "logs": self._gen_logs(target, fault_type, propagation, prop_deps),
            "alerts": self._gen_alerts(target, fault_type),
            "k8s_states": self._gen_k8s(target, fault_type),
            "service_graph": {"services": self.SERVICES[:], "edges": self.SERVICE_EDGES[:]},
            "metric_columns": [f"{s}-{m}" for s in self.SERVICES for m in ["cpu_usage","memory_usage_bytes","request_latency_p99","error_rate"]],
            "service_inventory": self.SERVICES[:],
            "deployment_info": {"namespaces": [self.NAMESPACE], "replicas": {s: 1 for s in self.SERVICES}},
        }

    def _detect_anomaly(self, case_id):
        svc = random.choice(self.SERVICES[:4])
        return self.get_case_detail.__wrapped__ if False else {
            "case_id": case_id, "case_name": f"Live anomaly in {svc}",
            "source": self.name, "source_type": self.source_type,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "severity": "warning",
            "fault_injection": {},
            "root_cause_ground_truth": f"{svc} is the root cause.",
            "metrics": self._gen_metrics(svc, "auto_detect", "none", []),
            "logs": self._gen_logs(svc, "auto_detect", "none", []),
            "alerts": self._gen_alerts(svc, "auto_detect"),
            "k8s_states": self._gen_k8s(svc, "auto_detect"),
            "service_graph": {"services": self.SERVICES[:], "edges": self.SERVICE_EDGES[:]},
            "metric_columns": [], "service_inventory": self.SERVICES[:],
            "deployment_info": {"namespaces": [self.NAMESPACE], "replicas": {s: 1 for s in self.SERVICES}},
        }

    def _gen_metrics(self, target, fault_type, propagation, prop_deps):
        """Generate metrics with genuine propagation effects."""
        series = []
        base = {"cpu_usage": (0.3, 0.1), "memory_usage_bytes": (256e6, 50e6),
                "request_latency_p99": (0.05, 0.02), "error_rate": (0.01, 0.005)}
        fault_impact = {"pod_crash": {"cpu_usage": 0, "error_rate": 1.0},
                        "high_cpu": {"cpu_usage": 0.95}, "high_latency": {"request_latency_p99": 2.0},
                        "memory_leak": {"memory_usage_bytes": 480e6},
                        "database_failure": {"error_rate": 0.85, "request_latency_p99": 4.0},
                        "network_partition": {"error_rate": 0.9, "request_latency_p99": 3.0},
                        "message_queue_failure": {"error_rate": 0.7, "request_latency_p99": 3.5}}
        impact = fault_impact.get(fault_type, {})

        for svc in self.SERVICES:
            is_target = (svc == target)
            is_affected = svc in prop_deps
            for metric, (mean, std) in base.items():
                val = mean
                if is_target and metric in impact:
                    val = impact[metric] if isinstance(impact[metric], (int, float)) else mean * 2
                elif is_affected and metric in impact and propagation in ("upstream", "bidirectional"):
                    # Affected services show MORE impact than root cause!
                    if isinstance(impact[metric], (int, float)):
                        val = impact[metric] * random.uniform(0.7, 1.2)
                    else:
                        val = mean * (1 + random.uniform(0.5, 1.0))
                else:
                    val = mean + random.uniform(-std, std)
                s = max(0, val + random.gauss(0, std * (2 if is_target or is_affected else 1)))
                series.append({"column": f"{svc}-{metric}", "service": svc,
                               "mean": round(val, 6), "std": round(std, 6),
                               "min": round(max(0, val - 3*std), 6),
                               "max": round(val + 3*std, 6),
                               "range": round(6*std + (val*0.5 if is_target or is_affected else 0), 6)})
        return {"series_summary": series[:200]}

    def _gen_logs(self, target, fault_type, propagation, prop_deps):
        """Generate logs with REALISTIC propagation pattern:
        - Root cause service: FEWER error logs (it's simply down/unresponsive)
        - Affected services: MORE error logs (actively failing while trying)
        This creates GENUINE ambiguity for RCA evaluation.
        """
        # Root cause log templates: passive failure (fewer, shorter)
        root_logs_pool = {"pod_crash": [("ERROR","{svc} process crashed")],
                  "high_cpu": [("WARN","{svc} CPU high")],
                  "high_latency": [("WARN","{svc} slow response")],
                  "memory_leak": [("ERROR","{svc} OOMKilled")],
                  "database_failure": [("ERROR","{svc} service stopped"), ("WARN","{svc} unresponsive")],
                  "network_partition": [("ERROR","{svc} isolated"), ("WARN","{svc} unreachable")],
                  "message_queue_failure": [("ERROR","{svc} broker down"), ("WARN","{svc} disconnected")]}

        # Affected service log templates: active failure (more, descriptive)
        affected_logs_pool = {"pod_crash": [("ERROR","{svc} connection refused to downstream")],
                  "high_cpu": [("WARN","{svc} CPU slow")],
                  "high_latency": [("WARN","{svc} downstream timeout 5000ms"), ("ERROR","{svc} retry exhausted")],
                  "memory_leak": [("ERROR","{svc} GC overhead")],
                  "database_failure": [("ERROR","{svc} DB connection refused"), ("ERROR","{svc} SQL timeout 30s"),
                                       ("ERROR","{svc} too many DB connection errors"), ("WARN","{svc} circuit breaker OPEN"),
                                       ("ERROR","{svc} query failed after 3 retries"), ("ERROR","{svc} cannot fetch data")],
                  "network_partition": [("ERROR","{svc} connection timed out 30s"), ("ERROR","{svc} ECONNREFUSED"),
                                        ("ERROR","{svc} downstream unreachable"), ("WARN","{svc} all retries failed")],
                  "message_queue_failure": [("ERROR","{svc} RabbitMQ connection refused"), ("ERROR","{svc} AMQP channel broken"),
                                            ("ERROR","{svc} message publish failed"), ("WARN","{svc} consumer disconnected")]}

        entries = []
        now = time.time()
        
        # Root cause: FEW errors (just crashed/stopped)
        root_t = root_logs_pool.get(fault_type, [("ERROR","{svc} failed")])
        for i in range(3):
            t = random.choice(root_t)
            entries.append({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.localtime(now-i*30)),
                           "service": target, "level": t[0], "message": t[1].format(svc=target)})
        
        # Affected services: MANY errors (actively failing)
        if propagation in ("upstream", "bidirectional"):
            aff_t = affected_logs_pool.get(fault_type, [("ERROR","{svc} downstream failure")])
            for svc in prop_deps:
                for i in range(8):  # MORE errors than root cause!
                    t = random.choice(aff_t)
                    entries.append({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.localtime(now-i*30)),
                                   "service": svc, "level": t[0], "message": t[1].format(svc=svc)})
        
        # Other services: normal
        for svc in self.SERVICES:
            if svc in [target] + prop_deps: continue
            for i in range(2):
                entries.append({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.localtime(now-random.randint(0,300))),
                               "service": svc, "level": "INFO", "message": f"{svc} OK"})
        entries.sort(key=lambda x: x["timestamp"])
        return {"entries": entries[:60]}

    def _gen_alerts(self, target, fault_type):
        return {"alerts": [{"name": fault_type, "severity": "critical", "message": f"Fault {fault_type} on {target}",
                           "service": target, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ")}], "alert_count": 1}

    def _gen_k8s(self, target, fault_type):
        return {"previews": [{"command": "kubectl get pods", "resource": "pods",
                "preview": f"{target}-xxx 0/1 Error 5 10m" if fault_type != "pod_crash" else f"{target}-xxx 0/1 CrashLoopBackOff 5 10m"}]}

    def health_check(self) -> Dict[str, Any]:
        return cluster_health(
            source_id="sock-shop",
            source_name=self.name,
            namespace=self.NAMESPACE,
        )
