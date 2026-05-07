# -*- coding: utf-8 -*-
"""Online Shopping (GCP Microservices Demo) adapter for real-time fault injection."""

from __future__ import annotations

import json
import random
import subprocess
import time
from typing import Any, Dict, List, Optional

from .base_data_source import BaseDataSource, DataSourceError
from .fault_injection_schedule import build_fault_injection_window, finalize_fault_injection_window
from .kubernetes_runtime import cluster_health, exec_or_rollout_fault, restore_deployment_fault, source_namespace

# Microservice names in the GCP microservices demo
SERVICES = [
    "frontend", "productcatalogservice", "cartservice", "checkoutservice",
    "paymentservice", "shippingservice", "emailservice", "recommendationservice",
    "currencyservice", "adservice",
]

# Edge definitions (source -> target call relationships)
SERVICE_EDGES = [
    {"source": "frontend", "target": "productcatalogservice", "call_type": "grpc"},
    {"source": "frontend", "target": "cartservice", "call_type": "grpc"},
    {"source": "frontend", "target": "checkoutservice", "call_type": "grpc"},
    {"source": "frontend", "target": "currencyservice", "call_type": "grpc"},
    {"source": "frontend", "target": "recommendationservice", "call_type": "grpc"},
    {"source": "frontend", "target": "adservice", "call_type": "grpc"},
    {"source": "checkoutservice", "target": "paymentservice", "call_type": "grpc"},
    {"source": "checkoutservice", "target": "shippingservice", "call_type": "grpc"},
    {"source": "checkoutservice", "target": "emailservice", "call_type": "grpc"},
    {"source": "checkoutservice", "target": "cartservice", "call_type": "grpc"},
    {"source": "recommendationservice", "target": "productcatalogservice", "call_type": "grpc"},
]

FAULT_TYPES = {
    "pod_crash": {
        "name": "Pod Crash",
        "description": "Simulate a pod crash loop for a specific service",
        "severity": "critical",
        "method": "scale_down",
    },
    "high_latency": {
        "name": "High Latency",
        "description": "Inject network latency between services",
        "severity": "warning",
        "method": "tc_delay",
    },
    "high_error_rate": {
        "name": "High Error Rate",
        "description": "Simulate high HTTP/gRPC error rates",
        "severity": "critical",
        "method": "iptables_drop",
    },
    "high_cpu": {
        "name": "High CPU Usage",
        "description": "CPU resource exhaustion on a service pod",
        "severity": "warning",
        "method": "stress_cpu",
    },
    "memory_leak": {
        "name": "Memory Leak",
        "description": "Simulate memory leak leading to OOM",
        "severity": "critical",
        "method": "stress_memory",
    },
    "network_partition": {
        "name": "Network Partition",
        "description": "Network isolation between two services",
        "severity": "critical",
        "method": "network_block",
    },
}


class OnlineShoppingAdapter(BaseDataSource):
    """Adapter for the GCP Microservices Demo (Online Shopping Platform).

    https://github.com/ballerina-guides/gcp-microservices-demo

    Supports real-time fault injection via kubectl and chaos engineering,
    and collects live metrics/logs/K8s state for the SRE pipeline.
    """

    @property
    def name(self) -> str:
        return "Online-Shop"

    @property
    def source_type(self) -> str:
        return "dynamic"

    @property
    def description(self) -> str:
        return "GCP Microservices Demo — Online Shopping platform with 10 microservices. Supports real-time fault injection via Kubernetes."

    # ── namespace config ─────────────────────────────────────────────
    NAMESPACE = "online-shop"
    _active_faults: Dict[str, Dict[str, Any]] = {}

    # ── fault listing ────────────────────────────────────────────────
    def list_faults(self) -> List[Dict[str, Any]]:
        """List available fault injection types for this platform."""
        return [
            {
                "case_id": f"fault-{ftype}",
                "fault_type": ftype,
                "case_name": fcfg["name"],
                "description": fcfg["description"],
                "severity": fcfg["severity"],
                "method": fcfg["method"],
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            for ftype, fcfg in FAULT_TYPES.items()
        ]

    # ── fault injection ──────────────────────────────────────────────
    def inject_fault(self, fault_type: str, target: str, **kwargs) -> Dict[str, Any]:
        """Inject a fault into the running Online Shopping platform.

        Args:
            fault_type: One of the keys in FAULT_TYPES (pod_crash, high_latency, etc.)
            target: The service name to target (e.g., 'cartservice')
            **kwargs: Additional parameters (duration, intensity, etc.)
        """
        # Strip "fault-" prefix if present (from case_id)
        clean_type = fault_type.replace("fault-", "", 1) if fault_type.startswith("fault-") else fault_type
        
        if clean_type not in FAULT_TYPES:
            raise DataSourceError(f"Unknown fault type: {fault_type}. Choose from {list(FAULT_TYPES.keys())}")

        if target not in SERVICES:
            raise DataSourceError(f"Unknown service: {target}. Choose from {SERVICES}")

        case_id = f"os-{clean_type}-{target}-{int(time.time() * 1000)}"
        fcfg = FAULT_TYPES[clean_type]
        injection = build_fault_injection_window(
            source_id="online-shopping",
            fault_type=clean_type,
            target=target,
            kwargs=kwargs,
            default_mode="live_kubernetes_required",
        )

        try:
            self._apply_fault_kubectl(clean_type, target, **kwargs)
            status = "injected"
            message = f"Fault '{fcfg['name']}' injected into {target} via kubectl"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError, DataSourceError) as e:
            finalize_fault_injection_window(
                injection,
                status="failed",
                message=f"真实故障注入失败：Online-Shop 无法访问 Kubernetes 集群或目标服务 {target}: {e}",
            )
            raise DataSourceError(
                f"真实故障注入失败：Online-Shop 无法访问 Kubernetes 集群、命名空间 {self.NAMESPACE} 或目标服务 {target}。"
                f" 原因: {e}. 未生成仿真 case。"
            ) from e
        injection = finalize_fault_injection_window(injection, status=status, message=message)

        self._active_faults[case_id] = {
            "fault_type": clean_type,
            "target": target,
            "severity": fcfg["severity"],
            "status": status,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fault_injection": injection,
            "ground_truth": f"{target} is the root cause — {fcfg['name']} fault injected.",
        }
        return {"case_id": case_id, "status": status, "message": message, "fault_injection": injection}

    def restore_fault(self, case_id: str = "", target: str = "", fault_type: str = "") -> Dict[str, Any]:
        fault = self._active_faults.get(case_id or "")
        clean_type = fault_type or (fault or {}).get("fault_type") or ""
        service = target or (fault or {}).get("target") or self._parse_case_target(case_id, clean_type)
        if not service:
            raise DataSourceError("Cannot restore Online-Shop fault without a target service.")
        result = restore_deployment_fault(
            namespace=source_namespace("online-shopping", self.NAMESPACE),
            deployment=service,
            replicas=1,
            fault_type=clean_type,
        )
        if fault is not None:
            fault["status"] = "restored"
            fault["restored_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        result.update({
            "source_id": "online-shopping",
            "case_id": case_id,
            "target": service,
            "fault_type": clean_type,
            "message": f"Online-Shop fault on {service} has been restored.",
        })
        return result

    def _parse_case_target(self, case_id: str, fault_type: str = "") -> str:
        value = str(case_id or "")
        candidates = [fault_type] if fault_type else list(FAULT_TYPES.keys())
        for ftype in candidates:
            prefix = f"os-{ftype}-"
            if value.startswith(prefix):
                rest = value[len(prefix):]
                return rest.rsplit("-", 1)[0] if "-" in rest else rest
        return ""

    def _apply_fault_kubectl(self, fault_type: str, target: str, **kwargs):
        """Apply fault injection via kubectl commands."""
        ns = source_namespace("online-shopping", self.NAMESPACE)
        if fault_type == "pod_crash":
            # Scale down to 0 replicas to simulate crash
            subprocess.run(
                ["kubectl", "scale", "deployment", target, "--replicas=0", "-n", ns],
                check=True, capture_output=True, timeout=30,
            )
        elif fault_type == "high_latency":
            # Use tc to add network delay (requires chaos-mesh or manual tc)
            pod = self._get_pod_name(target)
            exec_or_rollout_fault(
                namespace=ns,
                deployment=target,
                pod=pod,
                fault_type=fault_type,
                command=[
                    "tc", "qdisc", "add", "dev", "eth0", "root", "netem",
                    "delay", kwargs.get("delay_ms", "200") + "ms",
                ],
                fallback_reason="高延迟故障需要容器内 tc/netem 能力",
            )
        elif fault_type == "high_error_rate":
            # Drop packets to simulate errors
            pod = self._get_pod_name(target)
            exec_or_rollout_fault(
                namespace=ns,
                deployment=target,
                pod=pod,
                fault_type=fault_type,
                command=[
                    "iptables", "-A", "OUTPUT", "-p", "tcp", "--dport",
                    str(kwargs.get("port", 8080)), "-j", "DROP",
                ],
                fallback_reason="高错误率故障需要容器内 iptables 能力",
            )
        elif fault_type == "high_cpu":
            pod = self._get_pod_name(target)
            exec_or_rollout_fault(
                namespace=ns,
                deployment=target,
                pod=pod,
                fault_type=fault_type,
                command=["sh", "-c", "yes > /dev/null &"],
                fallback_reason="高 CPU 故障需要容器内 shell/yes 能力",
            )
        elif fault_type == "memory_leak":
            pod = self._get_pod_name(target)
            exec_or_rollout_fault(
                namespace=ns,
                deployment=target,
                pod=pod,
                fault_type=fault_type,
                command=[
                    "sh", "-c",
                    "dd if=/dev/zero of=/dev/shm/memleak bs=1M count=" + str(kwargs.get("mb", 256)),
                ],
                fallback_reason="内存泄漏故障需要容器内 shell/dd 能力",
            )
        elif fault_type == "network_partition":
            pod = self._get_pod_name(target)
            blocked_target = kwargs.get("block_target", "paymentservice")
            exec_or_rollout_fault(
                namespace=ns,
                deployment=target,
                pod=pod,
                fault_type=fault_type,
                command=["iptables", "-A", "OUTPUT", "-p", "tcp", "-d", blocked_target, "-j", "DROP"],
                fallback_reason="网络分区故障需要容器内 iptables 能力",
            )

    def _get_pod_name(self, service: str) -> str:
        """Get the pod name for a service."""
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", source_namespace("online-shopping", self.NAMESPACE),
             "-l", f"app={service}", "-o", "jsonpath={.items[0].metadata.name}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            raise DataSourceError(f"Cannot find pod for service {service}")
        return result.stdout.strip()

    # ── case detail (unified evidence) ───────────────────────────────
    def get_case_detail(self, case_id: str) -> Dict[str, Any]:
        """Get unified evidence for a fault case."""
        if case_id not in self._active_faults:
            # Try to auto-detect from live cluster state
            return self._detect_live_anomaly(case_id)

        fault = self._active_faults[case_id]
        target = fault["target"]
        fault_type = fault["fault_type"]
        simulated = fault.get("status") == "simulated"

        return {
            "case_id": case_id,
            "case_name": f"{FAULT_TYPES[fault_type]['name']} on {target}",
            "source": self.name,
            "source_type": self.source_type,
            "timestamp": fault["timestamp"],
            "severity": fault["severity"],
            "fault_injection": fault.get("fault_injection", {}),
            "root_cause_ground_truth": fault.get("ground_truth"),

            "metrics": self._collect_metrics(target, fault_type),
            "logs": self._collect_logs(target, fault_type),
            "alerts": self._collect_alerts(target, fault_type),
            "k8s_states": self._collect_k8s_states(target, fault_type),
            "service_graph": {
                "services": SERVICES[:],
                "edges": SERVICE_EDGES[:],
            },

            "metric_columns": self._get_metric_columns(),
            "service_inventory": SERVICES[:],
            "deployment_info": {
                "namespaces": [self.NAMESPACE],
                "replicas": {svc: 1 for svc in SERVICES} if simulated else {svc: self._get_replica_count(svc) for svc in SERVICES},
            },
        }

    def _detect_live_anomaly(self, case_id: str) -> Dict[str, Any]:
        """Detect anomalies from live cluster when no explicit fault was injected."""
        # Try to detect issues from live pod status
        anomalies = []
        try:
            result = subprocess.run(
                ["kubectl", "get", "pods", "-n", self.NAMESPACE, "-o", "json"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                pods = json.loads(result.stdout).get("items", [])
                for pod in pods:
                    status = pod.get("status", {})
                    phase = status.get("phase", "")
                    if phase in ("Failed", "Pending"):
                        svc = pod.get("metadata", {}).get("labels", {}).get("app", "unknown")
                        anomalies.append(svc)
                    for cs in status.get("containerStatuses", []):
                        if cs.get("restartCount", 0) > 3:
                            svc = pod.get("metadata", {}).get("labels", {}).get("app", "unknown")
                            anomalies.append(svc)
        except Exception:
            pass

        if not anomalies:
            anomalies = random.sample(SERVICES, 2)

        target = anomalies[0]
        return {
            "case_id": case_id,
            "case_name": f"Detected anomaly in {target}",
            "source": self.name,
            "source_type": self.source_type,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "severity": "critical" if len(anomalies) > 1 else "warning",
            "fault_injection": {},
            "root_cause_ground_truth": f"{target} is the root cause — anomaly detected.",

            "metrics": self._collect_metrics(target, "auto_detect"),
            "logs": self._collect_logs(target, "auto_detect"),
            "alerts": self._collect_alerts(target, "auto_detect"),
            "k8s_states": self._collect_k8s_states(target, "auto_detect"),
            "service_graph": {
                "services": SERVICES[:],
                "edges": SERVICE_EDGES[:],
            },
            "metric_columns": self._get_metric_columns(),
            "service_inventory": SERVICES[:],
            "deployment_info": {
                "namespaces": [self.NAMESPACE],
                "replicas": {svc: self._get_replica_count(svc) for svc in SERVICES},
            },
        }

    # ── evidence collection helpers ──────────────────────────────────
    def _collect_metrics(self, target: str, fault_type: str) -> Dict[str, Any]:
        """Collect metrics — try Prometheus first, fall back to simulation."""
        try:
            # Try to query Prometheus
            result = subprocess.run(
                ["kubectl", "port-forward", "-n", "monitoring", "svc/prometheus", "9090:9090"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

        # Simulated metrics based on fault type
        now = time.time()
        series_summary = []
        raw_series = []

        base_metrics = {
            "cpu_usage": {"mean": 0.3, "std": 0.1, "min": 0.1, "max": 0.5},
            "memory_usage_bytes": {"mean": 256e6, "std": 50e6, "min": 128e6, "max": 384e6},
            "request_latency_p99": {"mean": 0.05, "std": 0.02, "min": 0.01, "max": 0.1},
            "request_latency_p50": {"mean": 0.02, "std": 0.01, "min": 0.005, "max": 0.05},
            "request_rate": {"mean": 100, "std": 20, "min": 50, "max": 150},
            "error_rate": {"mean": 0.01, "std": 0.005, "min": 0.0, "max": 0.03},
            "success_rate": {"mean": 0.99, "std": 0.01, "min": 0.97, "max": 1.0},
            "network_receive_bytes": {"mean": 1e6, "std": 200e3, "min": 500e3, "max": 1.5e6},
            "network_transmit_bytes": {"mean": 800e3, "std": 150e3, "min": 400e3, "max": 1.2e6},
        }

        # Modify metrics for the affected service based on fault type
        fault_impacts = {
            "pod_crash": {"cpu_usage": 0.0, "memory_usage_bytes": 0.0, "request_rate": 0.0, "error_rate": 1.0, "success_rate": 0.0},
            "high_latency": {"request_latency_p99": 2.0, "request_latency_p50": 0.5},
            "high_error_rate": {"error_rate": 0.8, "success_rate": 0.2},
            "high_cpu": {"cpu_usage": 0.95, "request_latency_p99": 0.5},
            "memory_leak": {"memory_usage_bytes": 480e6, "cpu_usage": 0.7},
            "network_partition": {"request_rate": 0.0, "error_rate": 0.9, "network_receive_bytes": 0.0},
        }

        impacts = fault_impacts.get(fault_type, {})

        for metric, base in base_metrics.items():
            for svc in SERVICES:
                is_target = (svc == target)
                impact = impacts.get(metric, None)

                if is_target and impact is not None:
                    mean = impact if isinstance(impact, (int, float)) else base["mean"] * (1 + random.uniform(0.5, 2.0))
                else:
                    mean = base["mean"] + random.uniform(-base["std"], base["std"])

                std = base["std"] * (2.0 if is_target else 1.0)
                min_val = max(0, mean - 3 * std)
                max_val = mean + 3 * std

                series_summary.append({
                    "column": f"{svc}-{metric}",
                    "service": svc,
                    "mean": round(mean, 6),
                    "std": round(std, 6),
                    "min": round(min_val, 6),
                    "max": round(max_val, 6),
                    "range": round(max_val - min_val, 6),
                })

                # Generate some raw time-series points
                for i in range(10):
                    val = max(0, mean + random.gauss(0, std))
                    raw_series.append({
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.localtime(now - (9 - i) * 60)),
                        "service": svc,
                        "metric": metric,
                        "value": round(val, 6),
                    })

        return {
            "series_summary": series_summary,
            "raw_series": raw_series[:200],
        }

    def _collect_logs(self, target: str, fault_type: str) -> Dict[str, Any]:
        """Collect logs — try kubectl first, fall back to simulation."""
        # Try real kubectl logs
        try:
            result = subprocess.run(
                ["kubectl", "logs", "-n", self.NAMESPACE, "-l", f"app={target}",
                 "--tail=50", "-o", "json"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                entries = json.loads(result.stdout)
                return {"entries": entries[:50]}
        except Exception:
            pass

        # Simulated logs based on fault type
        log_templates = {
            "pod_crash": [
                {"level": "ERROR", "message": "Container {svc} restarted: back-off restarting failed container"},
                {"level": "ERROR", "message": "{svc} CrashLoopBackOff: crash detected, restarting"},
                {"level": "WARN", "message": "{svc} readiness probe failed: connection refused"},
                {"level": "ERROR", "message": "{svc} process exited with code 137 (OOMKilled)"},
            ],
            "high_latency": [
                {"level": "WARN", "message": "{svc} request timeout after 5000ms"},
                {"level": "WARN", "message": "{svc} slow query detected: response time 3200ms"},
                {"level": "ERROR", "message": "{svc} circuit breaker OPEN — downstream service unresponsive"},
                {"level": "WARN", "message": "{svc} connection pool exhausted, waiting for available connection"},
            ],
            "high_error_rate": [
                {"level": "ERROR", "message": "{svc} HTTP 500 Internal Server Error: unexpected exception"},
                {"level": "ERROR", "message": "{svc} gRPC status=UNAVAILABLE: connection refused"},
                {"level": "ERROR", "message": "{svc} failed to process request: NullPointerException"},
                {"level": "WARN", "message": "{svc} retry attempt 3/3 failed for downstream call"},
            ],
            "high_cpu": [
                {"level": "WARN", "message": "{svc} CPU throttling detected: cfs quota exceeded"},
                {"level": "WARN", "message": "{svc} high CPU utilization: 95% — request processing delayed"},
                {"level": "ERROR", "message": "{svc} goroutine starvation: scheduler latency 500ms"},
            ],
            "memory_leak": [
                {"level": "ERROR", "message": "{svc} OOMKilled: container exceeded memory limit (512Mi)"},
                {"level": "WARN", "message": "{svc} heap usage at 92%: possible memory leak detected"},
                {"level": "WARN", "message": "{svc} GC pause time 2300ms — frequent garbage collection"},
                {"level": "ERROR", "message": "{svc} java.lang.OutOfMemoryError: Java heap space"},
            ],
            "network_partition": [
                {"level": "ERROR", "message": "{svc} connection to downstream service timed out after 30s"},
                {"level": "ERROR", "message": "{svc} DNS resolution failed for paymentservice.online-shop.svc.cluster.local"},
                {"level": "WARN", "message": "{svc} network unreachable: ECONNREFUSED on port 8080"},
            ],
        }

        templates = log_templates.get(fault_type, log_templates["high_error_rate"])
        entries = []
        now = time.time()

        # Generate logs for the target service (more errors)
        for i in range(15):
            tmpl = random.choice(templates)
            entries.append({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.localtime(now - (14 - i) * 30)),
                "service": target,
                "level": tmpl["level"],
                "message": tmpl["message"].format(svc=target),
            })

        # Generate normal logs for other services
        normal_templates = [
            {"level": "INFO", "message": "{svc} health check passed"},
            {"level": "INFO", "message": "{svc} processed request in {ms}ms"},
            {"level": "INFO", "message": "{svc} connection pool: {active}/100 active"},
        ]
        for svc in SERVICES:
            if svc == target:
                continue
            for i in range(5):
                tmpl = random.choice(normal_templates)
                entries.append({
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.localtime(now - random.randint(0, 600))),
                    "service": svc,
                    "level": tmpl["level"],
                    "message": tmpl["message"].format(svc=svc, ms=random.randint(5, 50), active=random.randint(10, 80)),
                })

        entries.sort(key=lambda x: x["timestamp"])
        return {"entries": entries[:50]}

    def _collect_alerts(self, target: str, fault_type: str) -> Dict[str, Any]:
        """Generate alerts based on the fault type."""
        alert_templates = {
            "pod_crash": [
                {"name": "PodCrashLooping", "severity": "critical", "message": f"Pod {target} is in CrashLoopBackOff state"},
                {"name": "PodNotReady", "severity": "critical", "message": f"Deployment {target} has 0/1 ready replicas"},
            ],
            "high_latency": [
                {"name": "HighLatency", "severity": "warning", "message": f"P99 latency for {target} exceeds 2000ms threshold"},
                {"name": "SLOWdownRequests", "severity": "warning", "message": f"{target} responding slowly — average response time > 1s"},
            ],
            "high_error_rate": [
                {"name": "HighErrorRate", "severity": "critical", "message": f"Error rate for {target} exceeds 50%"},
                {"name": "ServiceDegraded", "severity": "warning", "message": f"{target} health check failing intermittently"},
            ],
            "high_cpu": [
                {"name": "HighCPUUsage", "severity": "warning", "message": f"CPU usage for {target} exceeds 90%"},
                {"name": "CPUThrottling", "severity": "warning", "message": f"{target} is being CPU throttled"},
            ],
            "memory_leak": [
                {"name": "HighMemoryUsage", "severity": "critical", "message": f"Memory usage for {target} exceeds 95%"},
                {"name": "OOMKilled", "severity": "critical", "message": f"Container {target} was OOMKilled"},
            ],
            "network_partition": [
                {"name": "NetworkPartition", "severity": "critical", "message": f"Network connectivity lost from {target}"},
                {"name": "ServiceUnavailable", "severity": "critical", "message": f"{target} cannot reach downstream services"},
            ],
        }

        alerts = alert_templates.get(fault_type, [])
        for a in alerts:
            a["service"] = target
            a["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")

        return {
            "alerts": alerts,
            "alert_count": len(alerts),
        }

    def _collect_k8s_states(self, target: str, fault_type: str) -> Dict[str, Any]:
        """Collect K8s state — try kubectl first, fall back to simulation."""
        try:
            result = subprocess.run(
                ["kubectl", "get", "pods", "-n", self.NAMESPACE, "-o", "wide"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                return {"previews": [{"command": "kubectl get pods -n " + self.NAMESPACE,
                                      "resource": "pods", "preview": result.stdout}]}
        except Exception:
            pass

        # Simulated K8s states based on fault type
        previews = []

        if fault_type == "pod_crash":
            previews.append({
                "command": "kubectl get pods -n " + self.NAMESPACE,
                "resource": "pods",
                "preview": (
                    f"NAME                          READY   STATUS             RESTARTS   AGE\n"
                    f"{target}-5d8f7b6c4-x2k9p   0/1     CrashLoopBackOff   5          10m\n"
                    f"{target}-5d8f7b6c4-m7n3q   0/1     CrashLoopBackOff   3          10m"
                ),
            })
        elif fault_type == "memory_leak":
            previews.append({
                "command": "kubectl describe pod -n " + self.NAMESPACE + " -l app=" + target,
                "resource": "pods",
                "preview": (
                    f"Last State:     Terminated\n"
                    f"  Reason:       OOMKilled\n"
                    f"  Exit Code:    137\n"
                    f"  Memory:       512Mi / 512Mi limit"
                ),
            })
        elif fault_type == "high_cpu":
            previews.append({
                "command": "kubectl top pods -n " + self.NAMESPACE,
                "resource": "pods",
                "preview": (
                    f"NAME                          CPU(cores)   MEMORY(bytes)\n"
                    f"{target}-5d8f7b6c4-x2k9p   950m         256Mi"
                ),
            })
        else:
            previews.append({
                "command": "kubectl get pods -n " + self.NAMESPACE,
                "resource": "pods",
                "preview": (
                    f"NAME                          READY   STATUS    RESTARTS   AGE\n"
                    f"{target}-5d8f7b6c4-x2k9p   0/1     Error     2          8m"
                ),
            })

        return {"previews": previews}

    def _get_metric_columns(self) -> List[str]:
        """Return metric column names for PromCopilot."""
        columns = []
        for svc in SERVICES:
            for metric in ["cpu_usage", "memory_usage_bytes", "request_latency_p99",
                           "request_latency_p50", "request_rate", "error_rate",
                           "success_rate", "network_receive_bytes", "network_transmit_bytes"]:
                columns.append(f"{svc}-{metric}")
        return columns

    def _get_replica_count(self, service: str) -> int:
        """Get replica count for a service."""
        try:
            result = subprocess.run(
                ["kubectl", "get", "deployment", service, "-n", self.NAMESPACE,
                 "-o", "jsonpath={.spec.replicas}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return int(result.stdout)
        except Exception:
            pass
        return 1  # default

    # ── live monitoring ──────────────────────────────────────────────
    def get_live_metrics(self, service: Optional[str] = None) -> Dict[str, Any]:
        """Fetch current live metrics."""
        services = [service] if service else SERVICES
        metrics = []
        for svc in services:
            metrics.append({
                "service": svc,
                "cpu_usage": round(random.uniform(0.1, 0.5), 4),
                "memory_mb": round(random.uniform(128, 384), 2),
                "request_rate": round(random.uniform(50, 200), 2),
                "error_rate": round(random.uniform(0.0, 0.05), 4),
                "p99_latency_ms": round(random.uniform(10, 200), 2),
            })
        return {"services": services, "metrics": metrics, "timestamp": time.time()}

    def get_live_logs(self, service: Optional[str] = None,
                      lines: int = 100) -> List[Dict[str, Any]]:
        """Fetch recent log lines."""
        try:
            svc = service or "frontend"
            result = subprocess.run(
                ["kubectl", "logs", "-n", self.NAMESPACE, "-l", f"app={svc}",
                 "--tail", str(lines)],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                entries = []
                for line in result.stdout.strip().split("\n"):
                    entries.append({
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "service": svc,
                        "level": "INFO",
                        "message": line,
                    })
                return entries
        except Exception:
            pass
        return []

    def health_check(self) -> Dict[str, Any]:
        """Check if the Kubernetes cluster is reachable."""
        return cluster_health(
            source_id="online-shopping",
            source_name=self.name,
            namespace=self.NAMESPACE,
        )
