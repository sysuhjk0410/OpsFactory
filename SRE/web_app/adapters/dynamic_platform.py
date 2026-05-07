# -*- coding: utf-8 -*-
"""
Dynamic Platform Adapters - Three microservice platforms with real-time fault injection.

1. Bank of Anthos (GCP microservices demo)
2. Sock Shop (microservices-demo / DeathStarBench)
3. Train Ticket (FudanSELab serverless-trainticket)

Each platform supports fault injection: network delay, pod crash, high CPU, high memory,
database errors, service unavailability.
"""

import abc
import json
import logging
import os
import random
import time
import threading
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class InjectedFault:
    fault_id: str
    platform: str
    fault_type: str
    target: str
    parameters: Dict[str, Any]
    start_time: float
    end_time: Optional[float] = None
    status: str = "active"  # active, resolved
    root_cause: str = ""
    logs: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    traces: List[Dict[str, Any]] = field(default_factory=list)
    ground_truth: str = ""


class FaultInjectionEngine:
    """
    Simulates fault injection for microservice platforms.
    In production, this would use chaos engineering tools (chaos-mesh, litmus).
    For demo/evaluation, it generates realistic fault data.
    """

    FAULT_TYPES = [
        "network_delay", "pod_crash", "high_cpu", "high_memory",
        "database_error", "service_unavailable", "disk_full", "dns_failure"
    ]

    def __init__(self):
        self._active_faults: Dict[str, InjectedFault] = {}
        self._fault_history: List[InjectedFault] = []
        self._lock = threading.Lock()
        self._next_id = 0

    def inject_fault(self, platform: str, fault_type: str, target: str,
                     parameters: Optional[Dict[str, Any]] = None) -> InjectedFault:
        """Inject a fault into a platform and return the fault object."""
        with self._lock:
            self._next_id += 1
            fault_id = f"fault-{platform}-{self._next_id:04d}"

            fault = InjectedFault(
                fault_id=fault_id,
                platform=platform,
                fault_type=fault_type,
                target=target,
                parameters=parameters or {},
                start_time=time.time(),
            )

            self._generate_ground_truth(fault)
            self._generate_fault_data(fault)
            self._active_faults[fault_id] = fault
            self._fault_history.append(fault)

            logger.info(f"Injected fault {fault_id}: {fault_type} on {target} (platform={platform})")
            return fault

    def resolve_fault(self, fault_id: str) -> Optional[InjectedFault]:
        """Resolve an active fault."""
        with self._lock:
            fault = self._active_faults.get(fault_id)
            if fault:
                fault.end_time = time.time()
                fault.status = "resolved"
                del self._active_faults[fault_id]
                logger.info(f"Resolved fault {fault_id}")
            return fault

    def get_active_faults(self, platform: str = "") -> List[InjectedFault]:
        with self._lock:
            if platform:
                return [f for f in self._active_faults.values() if f.platform == platform]
            return list(self._active_faults.values())

    def get_fault_history(self, platform: str = "", limit: int = 50) -> List[InjectedFault]:
        with self._lock:
            items = list(self._fault_history)
            if platform:
                items = [f for f in items if f.platform == platform]
            return items[-limit:]

    def get_fault(self, fault_id: str) -> Optional[InjectedFault]:
        return self._active_faults.get(fault_id) or next(
            (f for f in self._fault_history if f.fault_id == fault_id), None
        )

    def _generate_ground_truth(self, fault: InjectedFault):
        """Generate the ground truth root cause for this fault."""
        descriptions = {
            "network_delay": f"Network latency between {fault.target} and downstream services increased significantly, causing cascading timeouts",
            "pod_crash": f"Pod {fault.target} crashed due to {fault.parameters.get('reason', 'OOMKilled')}, causing service degradation",
            "high_cpu": f"CPU utilization on {fault.target} exceeded 90%, causing request processing delays",
            "high_memory": f"Memory leak in {fault.target} caused heap exhaustion and GC pressure",
            "database_error": f"Database connection pool exhausted on {fault.target}, causing query failures",
            "service_unavailable": f"Service {fault.target} became unreachable due to {fault.parameters.get('reason', 'config error')}",
            "disk_full": f"Disk space on {fault.target} reached 100%, causing write failures",
            "dns_failure": f"DNS resolution failure for {fault.target}, preventing service discovery",
        }
        fault.root_cause = descriptions.get(fault.fault_type, f"Unknown fault on {fault.target}")

    def _generate_fault_data(self, fault: InjectedFault):
        """Generate realistic log entries, metrics, and traces for the fault."""
        platform_info = PLATFORM_REGISTRY.get(fault.platform, {})
        services = platform_info.get("services", [])
        target_svc = fault.target if fault.target in services else (services[0] if services else "unknown")

        # Generate logs
        fault.logs = self._generate_logs(fault, target_svc)

        # Generate metrics
        fault.metrics = self._generate_metrics(fault, target_svc)

        # Generate traces
        fault.traces = self._generate_traces(fault, target_svc)

    def _generate_logs(self, fault: InjectedFault, service: str) -> List[str]:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(fault.start_time))
        logs = []
        templates = {
            "network_delay": [
                f'{ts} ERROR [{service}] Connection timeout after {fault.parameters.get("delay_ms", 5000)}ms to downstream service',
                f'{ts} WARN [{service}] Retry attempt 3/3 for request to downstream, latency > {fault.parameters.get("delay_ms", 5000)}ms',
                f'{ts} ERROR [{service}] Circuit breaker OPEN for downstream dependency - too many timeouts',
                f'{ts} WARN [{service}] Request queue growing: pending={random.randint(50, 200)}, avg_latency={fault.parameters.get("delay_ms", 5000)}ms',
            ],
            "pod_crash": [
                f'{ts} ERROR [kubelet] Pod {service} restarted: reason={fault.parameters.get("reason", "OOMKilled")}, exit_code=137',
                f'{ts} WARN [{service}] Container health check failed 3 consecutive times',
                f'{ts} ERROR [{service}] Process terminated unexpectedly with signal SIGKILL',
                f'{ts} INFO [{service}] Container restarting, attempt {random.randint(1, 5)}/5',
            ],
            "high_cpu": [
                f'{ts} WARN [{service}] CPU usage at {random.randint(85, 99)}%, above threshold 80%',
                f'{ts} ERROR [{service}] Request processing time degraded: p99={random.randint(2000, 10000)}ms (normal: 50ms)',
                f'{ts} WARN [{service}] Thread pool saturated: active={random.randint(190, 200)}/200, queued={random.randint(100, 500)}',
                f'{ts} ERROR [{service}] Slow query detected: duration={random.randint(5000, 30000)}ms',
            ],
            "high_memory": [
                f'{ts} WARN [{service}] Memory usage at {random.randint(85, 98)}% ({random.randint(700, 900)}/1024 MiB)',
                f'{ts} ERROR [{service}] GC pause duration: {random.randint(500, 5000)}ms (threshold: 200ms)',
                f'{ts} WARN [{service}] Heap utilization critical, triggering emergency GC',
                f'{ts} ERROR [{service}] OutOfMemoryError: Java heap space - unable to allocate {random.randint(10, 100)}MB',
            ],
            "database_error": [
                f'{ts} ERROR [{service}] Database connection pool exhausted: 0/{fault.parameters.get("pool_size", 20)} available',
                f'{ts} ERROR [{service}] Query timeout after {fault.parameters.get("timeout_ms", 30000)}ms: SELECT * FROM ...',
                f'{ts} WARN [{service}] DB connection wait time: {random.randint(5000, 30000)}ms (threshold: 1000ms)',
                f'{ts} ERROR [{service}] Too many connections to database, rejecting new requests',
            ],
            "service_unavailable": [
                f'{ts} ERROR [{service}] Service unavailable: connection refused to {fault.target}',
                f'{ts} ERROR [{service}] Health check failed for upstream {fault.target}: HTTP 503',
                f'{ts} WARN [{service}] Endpoint {fault.target} removed from load balancer pool',
                f'{ts} ERROR [{service}] Failed to resolve service {fault.target}: DNS lookup timeout',
            ],
            "disk_full": [
                f'{ts} ERROR [{service}] Disk usage at 100% on /data, no space left on device',
                f'{ts} ERROR [{service}] Failed to write log file: No space left on device',
                f'{ts} WARN [{service}] Write operations failing, disk space critical',
                f'{ts} ERROR [{service}] Unable to create temp file for request processing',
            ],
            "dns_failure": [
                f'{ts} ERROR [{service}] DNS resolution failed for {fault.target}: NXDOMAIN',
                f'{ts} ERROR [{service}] Service discovery failed: unable to resolve {fault.target}.default.svc.cluster.local',
                f'{ts} WARN [{service}] DNS cache expired, unable to refresh records for {fault.target}',
                f'{ts} ERROR [{service}] Connection failed: name or service not known for {fault.target}',
            ],
        }
        logs = templates.get(fault.fault_type, [f'{ts} ERROR [{service}] Unknown error'])
        # Add some normal logs for realism
        logs.extend([
            f'{ts} INFO [{service}] Processing request id={random.randint(10000, 99999)}',
            f'{ts} INFO [{service}] Health check passed',
        ])
        return logs

    def _generate_metrics(self, fault: InjectedFault, service: str) -> Dict[str, Any]:
        metrics = {
            "service": service,
            "platform": fault.platform,
            "fault_id": fault.fault_id,
            "timestamp": fault.start_time,
            "values": {}
        }

        if fault.fault_type == "network_delay":
            metrics["values"] = {
                "request_latency_p99_ms": random.uniform(3000, 10000),
                "request_latency_p50_ms": random.uniform(1000, 3000),
                "error_rate_percent": random.uniform(15, 60),
                "request_rate": random.uniform(50, 200),
                "connection_reuse_ratio": random.uniform(0.1, 0.3),
            }
        elif fault.fault_type == "pod_crash":
            metrics["values"] = {
                "pod_restart_count": random.randint(3, 20),
                "container_restarts_total": random.randint(3, 20),
                "request_latency_p99_ms": random.uniform(5000, 30000),
                "error_rate_percent": random.uniform(30, 80),
                "available_replicas": max(0, fault.parameters.get("replicas", 3) - random.randint(1, 3)),
            }
        elif fault.fault_type == "high_cpu":
            metrics["values"] = {
                "cpu_usage_percent": random.uniform(85, 99),
                "cpu_throttling_ratio": random.uniform(0.3, 0.8),
                "request_latency_p99_ms": random.uniform(2000, 10000),
                "request_rate": random.uniform(20, 100),
                "thread_count": random.randint(180, 200),
            }
        elif fault.fault_type == "high_memory":
            metrics["values"] = {
                "memory_usage_percent": random.uniform(85, 98),
                "memory_working_set_bytes": random.randint(800, 950) * 1024 * 1024,
                "gc_pause_ms": random.randint(500, 5000),
                "request_latency_p99_ms": random.uniform(3000, 15000),
                "error_rate_percent": random.uniform(10, 40),
            }
        elif fault.fault_type == "database_error":
            metrics["values"] = {
                "db_connection_pool_active": fault.parameters.get("pool_size", 20),
                "db_connection_pool_wait_ms": random.randint(5000, 30000),
                "db_query_duration_p99_ms": random.randint(10000, 60000),
                "error_rate_percent": random.uniform(20, 70),
                "request_latency_p99_ms": random.uniform(5000, 30000),
            }
        elif fault.fault_type == "service_unavailable":
            metrics["values"] = {
                "error_rate_percent": random.uniform(50, 100),
                "request_latency_p99_ms": random.uniform(10000, 60000),
                "upstream_health_check_failures": random.randint(10, 100),
                "connection_refused_total": random.randint(50, 500),
            }
        elif fault.fault_type == "disk_full":
            metrics["values"] = {
                "disk_usage_percent": 100.0,
                "disk_available_bytes": 0,
                "write_errors_total": random.randint(100, 1000),
                "error_rate_percent": random.uniform(30, 70),
                "request_latency_p99_ms": random.uniform(5000, 20000),
            }
        elif fault.fault_type == "dns_failure":
            metrics["values"] = {
                "dns_lookup_errors_total": random.randint(50, 500),
                "dns_lookup_latency_ms": random.uniform(5000, 30000),
                "error_rate_percent": random.uniform(40, 90),
                "request_latency_p99_ms": random.uniform(5000, 30000),
            }

        return metrics

    def _generate_traces(self, fault: InjectedFault, service: str) -> List[Dict[str, Any]]:
        traces = []
        for i in range(3):
            trace_id = f"{fault.fault_id}-trace-{i}"
            duration = random.randint(3000, 30000) if fault.fault_type in ("network_delay", "high_cpu") else random.randint(100, 2000)
            status = "ERROR" if random.random() < 0.6 else "OK"
            traces.append({
                "trace_id": trace_id,
                "service": service,
                "operation": random.choice(["HandleRequest", "ProcessOrder", "QueryDatabase", "CallUpstream"]),
                "duration_us": duration * 1000,
                "status": status,
                "tags": {
                    "http.status_code": 500 if status == "ERROR" else 200,
                    "error": fault.root_cause if status == "ERROR" else "",
                    "fault_type": fault.fault_type,
                },
            })
        return traces


# ──────────────────────────────────────────────
# Platform Registry - Three microservice platforms
# ──────────────────────────────────────────────

PLATFORM_REGISTRY = {
    "bank_of_anthos": {
        "name": "Bank of Anthos (GCP Microservices Demo)",
        "github": "https://github.com/ballerina-guides/gcp-microservices-demo",
        "description": "Google Cloud's microservices demo - banking application with account management, transaction processing, and frontend services",
        "services": [
            "frontend", "accounts", "ledger", "transaction-history",
            "balance-reader", "loadgenerator", "contacts", "user-service"
        ],
        "architecture": "Kubernetes + gRPC/REST",
        "languages": ["Java", "Go", "Python"],
        "fault_targets": ["frontend", "accounts", "ledger", "balance-reader"],
    },
    "sock_shop": {
        "name": "Sock Shop (Microservices Demo)",
        "github": "https://github.com/microservices-demo/microservices-demo",
        "description": "DeathStarBench microservices demo - e-commerce application with shopping cart, order processing, and payment services",
        "services": [
            "front-end", "carts", "orders", "catalogue", "user",
            "payment", "shipping", "queue-master", "rabbitmq", "mongodb"
        ],
        "architecture": "Kubernetes + RabbitMQ + MongoDB",
        "languages": ["Java", "Go", "Node.js"],
        "fault_targets": ["front-end", "carts", "orders", "catalogue", "payment"],
    },
    "train_ticket": {
        "name": "Train Ticket (Serverless Microservices)",
        "github": "https://github.com/FudanSELab/serverless-trainticket",
        "description": "Fudan SE Lab's serverless train ticket booking system - complex microservice architecture for ticket query, booking, and payment",
        "services": [
            "ts-ui-dashboard", "ts-basic-service", "ts-route-service",
            "ts-order-service", "ts-payment-service", "ts-user-service",
            "ts-notification-service", "ts-config-service", "ts-station-service"
        ],
        "architecture": "Kubernetes + Serverless Functions",
        "languages": ["Java", "Node.js", "Python"],
        "fault_targets": ["ts-basic-service", "ts-order-service", "ts-route-service", "ts-payment-service"],
    },
}


class DynamicPlatformAdapter:
    """Adapter for a specific dynamic microservice platform."""

    def __init__(self, platform_id: str, engine: FaultInjectionEngine):
        self.platform_id = platform_id
        self._info = PLATFORM_REGISTRY.get(platform_id, {})
        self._engine = engine

    def get_info(self) -> Dict[str, Any]:
        return {
            "platform_id": self.platform_id,
            "name": self._info.get("name", self.platform_id),
            "github": self._info.get("github", ""),
            "description": self._info.get("description", ""),
            "services": self._info.get("services", []),
            "architecture": self._info.get("architecture", ""),
            "languages": self._info.get("languages", []),
            "fault_targets": self._info.get("fault_targets", []),
            "fault_types": FaultInjectionEngine.FAULT_TYPES,
        }

    def inject_fault(self, fault_type: str, target: str,
                     parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        fault = self._engine.inject_fault(self.platform_id, fault_type, target, parameters)
        return self._fault_to_dict(fault)

    def get_active_faults(self) -> List[Dict[str, Any]]:
        return [self._fault_to_dict(f) for f in self._engine.get_active_faults(self.platform_id)]

    def get_fault_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [self._fault_to_dict(f) for f in self._engine.get_fault_history(self.platform_id, limit)]

    def resolve_fault(self, fault_id: str) -> Dict[str, Any]:
        fault = self._engine.resolve_fault(fault_id)
        return self._fault_to_dict(fault) if fault else {}

    def get_ground_truth(self, fault_id: str) -> str:
        fault = self._engine.get_fault(fault_id)
        return fault.root_cause if fault else ""

    def get_rca_context(self, fault_id: str) -> Dict[str, Any]:
        """Build RCA context from fault data for OpsAug and other tools."""
        fault = self._engine.get_fault(fault_id)
        if not fault:
            return {}
        return {
            "fault_id": fault.fault_id,
            "platform": fault.platform,
            "fault_type": fault.fault_type,
            "target": fault.target,
            "root_cause_ground_truth": fault.root_cause,
            "logs": fault.logs,
            "metrics": fault.metrics,
            "traces": fault.traces,
            "services": self._info.get("services", []),
            "architecture": self._info.get("architecture", ""),
        }

    def build_query(self, fault_id: str) -> str:
        """Build a natural language query for RCA based on the fault."""
        fault = self._engine.get_fault(fault_id)
        if not fault:
            return ""
        return (
            f"在 {self._info.get('name', fault.platform)} 平台中，"
            f"服务 {fault.target} 出现了故障。"
            f"故障类型为 {fault.fault_type}，请进行根因分析。"
        )

    @staticmethod
    def _fault_to_dict(fault: InjectedFault) -> Dict[str, Any]:
        return {
            "fault_id": fault.fault_id,
            "platform": fault.platform,
            "fault_type": fault.fault_type,
            "target": fault.target,
            "parameters": fault.parameters,
            "start_time": fault.start_time,
            "end_time": fault.end_time,
            "status": fault.status,
            "root_cause": fault.root_cause,
            "log_count": len(fault.logs),
            "trace_count": len(fault.traces),
            "metrics_keys": list(fault.metrics.get("values", {}).keys()),
        }
