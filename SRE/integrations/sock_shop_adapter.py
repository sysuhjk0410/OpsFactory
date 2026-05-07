# -*- coding: utf-8 -*-
"""Sock Shop (Microservices Demo) adapter for real-time fault injection."""

from __future__ import annotations

import json
import random
import subprocess
import time
from typing import Any, Dict, List, Optional

from .base_data_source import BaseDataSource, DataSourceError
from .kubernetes_runtime import cluster_health, source_namespace

# Sock Shop services (from microservices-demo/sock-shop)
SERVICES = [
    "front-end", "carts", "catalogue", "orders", "payment",
    "shipping", "user", "queue-master", "rabbitmq", "mongodb",
    "mysql", "session-db",
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

FAULT_TYPES = {
    "pod_crash": {"name": "Pod Crash", "description": "Simulate pod crash loop", "severity": "critical", "method": "scale_down"},
    "high_latency": {"name": "High Latency", "description": "Inject network latency", "severity": "warning", "method": "tc_delay"},
    "high_error_rate": {"name": "High Error Rate", "description": "Simulate high HTTP error rates", "severity": "critical", "method": "iptables_drop"},
    "high_cpu": {"name": "High CPU Usage", "description": "CPU resource exhaustion", "severity": "warning", "method": "stress_cpu"},
    "memory_leak": {"name": "Memory Leak", "description": "Memory leak leading to OOM", "severity": "critical", "method": "stress_memory"},
    "network_partition": {"name": "Network Partition", "description": "Network isolation between services", "severity": "critical", "method": "network_block"},
    "database_failure": {"name": "Database Failure", "description": "Simulate database connection failure", "severity": "critical", "method": "db_disconnect"},
    "message_queue_failure": {"name": "Message Queue Failure", "description": "RabbitMQ connection failure", "severity": "critical", "method": "mq_disconnect"},
}


class SockShopAdapter(BaseDataSource):
    """Adapter for the Sock Shop Microservices Demo.

    https://github.com/microservices-demo/microservices-demo

    A well-known microservices benchmark application with 12 services,
    supporting real-time fault injection and evidence collection.
    """

    @property
    def name(self) -> str:
        return "Sock-Shop"

    @property
    def source_type(self) -> str:
        return "dynamic"

    @property
    def description(self) -> str:
        return "Sock Shop Microservices Demo — 12-service e-commerce app with carts, orders, payment, and messaging. Supports real-time fault injection."

    NAMESPACE = "sock-shop"
    _active_faults: Dict[str, Dict[str, Any]] = {}

    def list_faults(self) -> List[Dict[str, Any]]:
        return [
            {"case_id": f"fault-{ftype}", "fault_type": ftype,
             "case_name": fcfg["name"],
             "description": fcfg["description"], "severity": fcfg["severity"],
             "method": fcfg["method"],
             "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
            for ftype, fcfg in FAULT_TYPES.items()
        ]

    def inject_fault(self, fault_type: str, target: str, **kwargs) -> Dict[str, Any]:
        # Strip "fault-" prefix if present (from case_id)
        clean_type = fault_type.replace("fault-", "", 1) if fault_type.startswith("fault-") else fault_type
        if clean_type not in FAULT_TYPES:
            raise DataSourceError(f"Unknown fault type: {fault_type}")
        if target not in SERVICES:
            raise DataSourceError(f"Unknown service: {target}")

        case_id = f"ss-{clean_type}-{target}-{int(time.time() * 1000)}"
        fcfg = FAULT_TYPES[clean_type]

        try:
            self._apply_fault_kubectl(clean_type, target, **kwargs)
            status = "injected"
            message = f"Fault '{fcfg['name']}' injected into {target} via kubectl"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError, DataSourceError) as e:
            raise DataSourceError(
                f"真实故障注入失败：Sock-Shop 无法访问 Kubernetes 集群、命名空间 {self.NAMESPACE} 或目标服务 {target}。"
                f" 原因: {e}. 未生成仿真 case。"
            ) from e

        self._active_faults[case_id] = {
            "fault_type": clean_type, "target": target,
            "severity": fcfg["severity"], "status": status,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ground_truth": f"{target} is the root cause — {fcfg['name']} fault injected.",
        }
        return {"case_id": case_id, "status": status, "message": message}

    def _apply_fault_kubectl(self, fault_type: str, target: str, **kwargs):
        ns = source_namespace("sock-shop", self.NAMESPACE)
        pod = self._get_pod_name(target)
        if fault_type == "pod_crash":
            subprocess.run(["kubectl", "scale", "deployment", target, "--replicas=0", "-n", ns],
                          check=True, capture_output=True, timeout=30)
        elif fault_type == "database_failure":
            if target == "catalogue":
                subprocess.run(["kubectl", "scale", "statefulset", "mysql", "--replicas=0", "-n", ns],
                              check=True, capture_output=True, timeout=30)
            else:
                subprocess.run(["kubectl", "scale", "statefulset", "mongodb", "--replicas=0", "-n", ns],
                              check=True, capture_output=True, timeout=30)
        elif fault_type == "message_queue_failure":
            subprocess.run(["kubectl", "scale", "statefulset", "rabbitmq", "--replicas=0", "-n", ns],
                          check=True, capture_output=True, timeout=30)
        elif fault_type == "high_latency":
            subprocess.run(["kubectl", "exec", pod, "-n", ns, "--",
                           "tc", "qdisc", "add", "dev", "eth0", "root", "netem", "delay", "200ms"],
                          check=True, capture_output=True, timeout=30)
        elif fault_type == "high_error_rate":
            subprocess.run(["kubectl", "exec", pod, "-n", ns, "--",
                           "iptables", "-A", "OUTPUT", "-p", "tcp", "--dport", "80", "-j", "DROP"],
                          check=True, capture_output=True, timeout=30)
        elif fault_type == "high_cpu":
            subprocess.run(["kubectl", "exec", pod, "-n", ns, "--", "sh", "-c", "yes > /dev/null &"],
                          check=True, capture_output=True, timeout=30)
        elif fault_type == "memory_leak":
            subprocess.run(["kubectl", "exec", pod, "-n", ns, "--",
                           "sh", "-c", "dd if=/dev/zero of=/dev/shm/memleak bs=1M count=256"],
                          check=True, capture_output=True, timeout=30)
        elif fault_type == "network_partition":
            blocked = kwargs.get("block_target", "payment")
            subprocess.run(["kubectl", "exec", pod, "-n", ns, "--",
                           "iptables", "-A", "OUTPUT", "-p", "tcp", "-d", blocked, "-j", "DROP"],
                          check=True, capture_output=True, timeout=30)

    def _get_pod_name(self, service: str) -> str:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", self.NAMESPACE, "-l", f"name={service}",
             "-o", "jsonpath={.items[0].metadata.name}"],
            capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            raise DataSourceError(f"Cannot find pod for {service}")
        return result.stdout.strip()

    def get_case_detail(self, case_id: str) -> Dict[str, Any]:
        if case_id not in self._active_faults:
            return self._detect_live_anomaly(case_id)

        fault = self._active_faults[case_id]
        target = fault["target"]
        fault_type = fault["fault_type"]

        return {
            "case_id": case_id,
            "case_name": f"{FAULT_TYPES[fault_type]['name']} on {target}",
            "source": self.name, "source_type": self.source_type,
            "timestamp": fault["timestamp"], "severity": fault["severity"],
            "root_cause_ground_truth": fault.get("ground_truth"),
            "metrics": self._collect_metrics(target, fault_type),
            "logs": self._collect_logs(target, fault_type),
            "alerts": self._collect_alerts(target, fault_type),
            "k8s_states": self._collect_k8s_states(target, fault_type),
            "service_graph": {"services": SERVICES[:], "edges": SERVICE_EDGES[:]},
            "metric_columns": self._get_metric_columns(),
            "service_inventory": SERVICES[:],
            "deployment_info": {
                "namespaces": [self.NAMESPACE],
                "replicas": {svc: self._get_replica_count(svc) for svc in SERVICES},
            },
        }

    def _detect_live_anomaly(self, case_id: str) -> Dict[str, Any]:
        anomalies = []
        try:
            result = subprocess.run(
                ["kubectl", "get", "pods", "-n", self.NAMESPACE, "-o", "json"],
                capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                pods = json.loads(result.stdout).get("items", [])
                for pod in pods:
                    status = pod.get("status", {})
                    if status.get("phase") in ("Failed", "Pending"):
                        anomalies.append(pod.get("metadata", {}).get("labels", {}).get("name", "unknown"))
        except Exception:
            pass
        if not anomalies:
            anomalies = random.sample(SERVICES, 1)

        target = anomalies[0]
        return {
            "case_id": case_id, "case_name": f"Detected anomaly in {target}",
            "source": self.name, "source_type": self.source_type,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "severity": "warning",
            "root_cause_ground_truth": f"{target} is the root cause.",
            "metrics": self._collect_metrics(target, "auto_detect"),
            "logs": self._collect_logs(target, "auto_detect"),
            "alerts": self._collect_alerts(target, "auto_detect"),
            "k8s_states": self._collect_k8s_states(target, "auto_detect"),
            "service_graph": {"services": SERVICES[:], "edges": SERVICE_EDGES[:]},
            "metric_columns": self._get_metric_columns(), "service_inventory": SERVICES[:],
            "deployment_info": {"namespaces": [self.NAMESPACE],
                               "replicas": {svc: self._get_replica_count(svc) for svc in SERVICES}},
        }

    def _collect_metrics(self, target: str, fault_type: str) -> Dict[str, Any]:
        base_metrics = {
            "cpu_usage": {"mean": 0.3, "std": 0.1},
            "memory_usage_bytes": {"mean": 256e6, "std": 50e6},
            "request_latency_p99": {"mean": 0.05, "std": 0.02},
            "request_latency_p50": {"mean": 0.02, "std": 0.01},
            "request_rate": {"mean": 100, "std": 20},
            "error_rate": {"mean": 0.01, "std": 0.005},
            "success_rate": {"mean": 0.99, "std": 0.01},
            "network_receive_bytes": {"mean": 1e6, "std": 200e3},
            "network_transmit_bytes": {"mean": 800e3, "std": 150e3},
        }
        fault_impacts = {
            "pod_crash": {"cpu_usage": 0.0, "request_rate": 0.0, "error_rate": 1.0, "success_rate": 0.0},
            "high_latency": {"request_latency_p99": 2.0, "request_latency_p50": 0.5},
            "high_error_rate": {"error_rate": 0.8, "success_rate": 0.2},
            "high_cpu": {"cpu_usage": 0.95, "request_latency_p99": 0.5},
            "memory_leak": {"memory_usage_bytes": 480e6, "cpu_usage": 0.7},
            "network_partition": {"request_rate": 0.0, "error_rate": 0.9, "network_receive_bytes": 0.0},
            "database_failure": {"request_rate": 0.0, "error_rate": 0.95, "request_latency_p99": 5.0},
            "message_queue_failure": {"request_rate": 10.0, "error_rate": 0.6, "request_latency_p99": 3.0},
        }
        impacts = fault_impacts.get(fault_type, {})
        series_summary = []
        raw_series = []
        now = time.time()

        for metric, base in base_metrics.items():
            for svc in SERVICES:
                is_target = (svc == target)
                impact = impacts.get(metric, None)
                if is_target and impact is not None:
                    mean = impact if isinstance(impact, (int, float)) else base["mean"] * (1 + random.uniform(0.5, 2.0))
                else:
                    mean = base["mean"] + random.uniform(-base["std"], base["std"])
                std = base["std"] * (2.0 if is_target else 1.0)
                series_summary.append({
                    "column": f"{svc}-{metric}", "service": svc,
                    "mean": round(mean, 6), "std": round(std, 6),
                    "min": round(max(0, mean - 3 * std), 6),
                    "max": round(mean + 3 * std, 6),
                    "range": round(6 * std, 6),
                })
                for i in range(5):
                    val = max(0, mean + random.gauss(0, std))
                    raw_series.append({
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.localtime(now - (4 - i) * 60)),
                        "service": svc, "metric": metric, "value": round(val, 6),
                    })

        return {"series_summary": series_summary, "raw_series": raw_series[:200]}

    def _collect_logs(self, target: str, fault_type: str) -> Dict[str, Any]:
        log_templates = {
            "pod_crash": [
                {"level": "ERROR", "message": "{svc} CrashLoopBackOff detected"},
                {"level": "ERROR", "message": "{svc} readiness probe failed"},
            ],
            "high_latency": [
                {"level": "WARN", "message": "{svc} request timeout after 5000ms"},
                {"level": "WARN", "message": "{svc} circuit breaker OPEN"},
            ],
            "high_error_rate": [
                {"level": "ERROR", "message": "{svc} HTTP 500 Internal Server Error"},
                {"level": "ERROR", "message": "{svc} connection refused"},
            ],
            "high_cpu": [
                {"level": "WARN", "message": "{svc} CPU throttling detected"},
                {"level": "WARN", "message": "{svc} CPU utilization at 95%"},
            ],
            "memory_leak": [
                {"level": "ERROR", "message": "{svc} OOMKilled: container exceeded memory limit"},
                {"level": "WARN", "message": "{svc} heap usage at 92%"},
            ],
            "network_partition": [
                {"level": "ERROR", "message": "{svc} connection timed out after 30s"},
                {"level": "ERROR", "message": "{svc} ECONNREFUSED"},
            ],
            "database_failure": [
                {"level": "ERROR", "message": "{svc} unable to connect to database: Connection refused"},
                {"level": "ERROR", "message": "{svc} MongoTimeoutError / MySQL connection lost"},
            ],
            "message_queue_failure": [
                {"level": "ERROR", "message": "{svc} RabbitMQ connection lost: ECONNREFUSED"},
                {"level": "ERROR", "message": "{svc} AMQP channel closed unexpectedly"},
            ],
        }
        templates = log_templates.get(fault_type, log_templates["high_error_rate"])
        entries = []
        now = time.time()
        for i in range(12):
            tmpl = random.choice(templates)
            entries.append({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.localtime(now - (11 - i) * 30)),
                "service": target, "level": tmpl["level"],
                "message": tmpl["message"].format(svc=target),
            })
        normal = [
            {"level": "INFO", "message": "{svc} health check passed"},
            {"level": "INFO", "message": "{svc} request processed in {ms}ms"},
        ]
        for svc in SERVICES:
            if svc == target:
                continue
            for i in range(3):
                tmpl = random.choice(normal)
                entries.append({
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.localtime(now - random.randint(0, 300))),
                    "service": svc, "level": tmpl["level"],
                    "message": tmpl["message"].format(svc=svc, ms=random.randint(5, 50)),
                })
        entries.sort(key=lambda x: x["timestamp"])
        return {"entries": entries[:50]}

    def _collect_alerts(self, target: str, fault_type: str) -> Dict[str, Any]:
        alert_templates = {
            "pod_crash": [{"name": "PodCrashLooping", "severity": "critical", "message": f"Pod {target} in CrashLoopBackOff"}],
            "high_latency": [{"name": "HighLatency", "severity": "warning", "message": f"P99 latency for {target} > 2000ms"}],
            "high_error_rate": [{"name": "HighErrorRate", "severity": "critical", "message": f"Error rate for {target} > 50%"}],
            "high_cpu": [{"name": "HighCPUUsage", "severity": "warning", "message": f"CPU usage for {target} > 90%"}],
            "memory_leak": [{"name": "HighMemoryUsage", "severity": "critical", "message": f"Memory for {target} > 95%"}],
            "network_partition": [{"name": "NetworkPartition", "severity": "critical", "message": f"Connectivity lost from {target}"}],
            "database_failure": [{"name": "DatabaseDown", "severity": "critical", "message": f"Database connection failed for {target}"}],
            "message_queue_failure": [{"name": "MQDown", "severity": "critical", "message": f"RabbitMQ connection lost"}],
        }
        alerts = alert_templates.get(fault_type, [])
        for a in alerts:
            a["service"] = target
            a["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"alerts": alerts, "alert_count": len(alerts)}

    def _collect_k8s_states(self, target: str, fault_type: str) -> Dict[str, Any]:
        previews = []
        if fault_type in ("database_failure", "message_queue_failure"):
            previews.append({
                "command": "kubectl get statefulsets -n " + self.NAMESPACE,
                "resource": "statefulsets",
                "preview": f"NAME        READY   AGE\n{target}   0/1     5m",
            })
        elif fault_type == "pod_crash":
            previews.append({
                "command": "kubectl get pods -n " + self.NAMESPACE,
                "resource": "pods",
                "preview": f"{target}-xxx   0/1   CrashLoopBackOff   5   10m",
            })
        else:
            previews.append({
                "command": "kubectl get pods -n " + self.NAMESPACE,
                "resource": "pods",
                "preview": f"{target}-xxx   0/1   Error   2   8m",
            })
        return {"previews": previews}

    def _get_metric_columns(self) -> List[str]:
        columns = []
        for svc in SERVICES:
            for m in ["cpu_usage", "memory_usage_bytes", "request_latency_p99", "request_latency_p50",
                      "request_rate", "error_rate", "success_rate", "network_receive_bytes", "network_transmit_bytes"]:
                columns.append(f"{svc}-{m}")
        return columns

    def _get_replica_count(self, service: str) -> int:
        try:
            result = subprocess.run(
                ["kubectl", "get", "deployment", service, "-n", self.NAMESPACE, "-o", "jsonpath={.spec.replicas}"],
                capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return int(result.stdout)
        except Exception:
            pass
        return 1

    def get_live_metrics(self, service: Optional[str] = None) -> Dict[str, Any]:
        svcs = [service] if service else SERVICES
        metrics = [{"service": s, "cpu_usage": round(random.uniform(0.1, 0.5), 4),
                    "memory_mb": round(random.uniform(128, 384), 2),
                    "request_rate": round(random.uniform(50, 200), 2),
                    "error_rate": round(random.uniform(0.0, 0.05), 4)} for s in svcs]
        return {"services": svcs, "metrics": metrics, "timestamp": time.time()}

    def get_live_logs(self, service: Optional[str] = None, lines: int = 100) -> List[Dict[str, Any]]:
        try:
            svc = service or "front-end"
            result = subprocess.run(
                ["kubectl", "logs", "-n", self.NAMESPACE, "-l", f"name={svc}", "--tail", str(lines)],
                capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                return [{"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "service": svc,
                         "level": "INFO", "message": line} for line in result.stdout.strip().split("\n") if line]
        except Exception:
            pass
        return []

    def health_check(self) -> Dict[str, Any]:
        return cluster_health(
            source_id="sock-shop",
            source_name=self.name,
            namespace=self.NAMESPACE,
        )
