"""
Simulated infrastructure state, service metrics, logs, and mock environment for testing.
"""
import time
from typing import Dict, Any, Optional, List


class MockInfrastructureCluster:
    """In-memory mock infrastructure simulating Kubernetes / Cloud services."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.services: Dict[str, Dict[str, Any]] = {
            "payment-service": {
                "environment": "prod",
                "tier": "tier-1",
                "owner": "checkout-eng@corp.internal",
                "current_replicas": 3,
                "max_replicas": 12,
                "min_replicas": 2,
                "status": "DEGRADED",
                "restart_count": 4,
                "last_restart": "2026-09-03T08:00:00Z",
                "image": "registry.corp.internal/payment-service:v2.4.1",
                "cpu_utilization_pct": 88.5,
                "memory_utilization_pct": 96.2,  # Memory leak detected
                "error_rate_pct": 19.4,
                "p99_latency_ms": 4820,
                "active_connections": 1420,
                "dependencies": ["auth-service", "postgres-payment-db", "notification-service"],
                "dependents": ["order-service", "mobile-api-gateway"],
                "runbook_id": "RB-PAYMENT-002"
            },
            "auth-service": {
                "environment": "prod",
                "tier": "tier-0",  # Core mission critical
                "owner": "security-infra@corp.internal",
                "current_replicas": 4,
                "max_replicas": 10,
                "min_replicas": 3,
                "status": "DEGRADED",
                "restart_count": 1,
                "last_restart": "2026-09-02T12:00:00Z",
                "image": "registry.corp.internal/auth-service:v3.1.0",
                "cpu_utilization_pct": 94.1,
                "memory_utilization_pct": 62.0,
                "error_rate_pct": 24.1,
                "p99_latency_ms": 3100,
                "active_connections": 4800,
                "dependencies": ["redis-auth-cluster", "user-db"],
                "dependents": ["payment-service", "order-service", "user-profile-service"],
                "runbook_id": "RB-AUTH-001"
            },
            "order-service": {
                "environment": "prod",
                "tier": "tier-1",
                "owner": "order-eng@corp.internal",
                "current_replicas": 4,
                "max_replicas": 15,
                "min_replicas": 2,
                "status": "WARNING",
                "restart_count": 0,
                "last_restart": "2026-09-01T00:00:00Z",
                "image": "registry.corp.internal/order-service:v1.9.8",
                "cpu_utilization_pct": 45.0,
                "memory_utilization_pct": 52.0,
                "error_rate_pct": 8.2,
                "p99_latency_ms": 2400,
                "active_connections": 800,
                "dependencies": ["payment-service", "inventory-service"],
                "dependents": ["web-frontend", "mobile-app"],
                "runbook_id": "RB-ORDER-001"
            },
            "notification-service": {
                "environment": "prod",
                "tier": "tier-2",
                "owner": "comms-team@corp.internal",
                "current_replicas": 2,
                "max_replicas": 6,
                "min_replicas": 1,
                "status": "HEALTHY",
                "restart_count": 0,
                "last_restart": "2026-08-20T00:00:00Z",
                "image": "registry.corp.internal/notification-service:v1.1.2",
                "cpu_utilization_pct": 22.0,
                "memory_utilization_pct": 34.0,
                "error_rate_pct": 0.05,
                "p99_latency_ms": 110,
                "active_connections": 120,
                "dependencies": ["rabbitmq-cluster"],
                "dependents": ["payment-service", "order-service"],
                "runbook_id": "RB-NOTIF-001"
            },
            "staging-payment-service": {
                "environment": "staging",
                "tier": "tier-3",
                "owner": "checkout-eng@corp.internal",
                "current_replicas": 1,
                "max_replicas": 4,
                "min_replicas": 1,
                "status": "HEALTHY",
                "restart_count": 0,
                "last_restart": "2026-09-03T01:00:00Z",
                "image": "registry.corp.internal/payment-service:v2.4.2-rc1",
                "cpu_utilization_pct": 15.0,
                "memory_utilization_pct": 28.0,
                "error_rate_pct": 0.0,
                "p99_latency_ms": 95,
                "active_connections": 10,
                "dependencies": ["staging-auth-service"],
                "dependents": [],
                "runbook_id": "RB-PAYMENT-002"
            }
        }

        self.logs_db: Dict[str, List[Dict[str, Any]]] = {
            "payment-service": [
                {"timestamp": "2026-09-03T08:40:12Z", "level": "WARN", "message": "Heap memory reached 88% threshold. Initiating GC cycle."},
                {"timestamp": "2026-09-03T08:41:05Z", "level": "ERROR", "message": "java.lang.OutOfMemoryError: Java heap space at PaymentSessionPool.allocate(PaymentSession.java:142)"},
                {"timestamp": "2026-09-03T08:41:45Z", "level": "ERROR", "message": "CircuitBreaker(stripe-gateway) tripped: 45 consecutive timeouts. Downstream queue backpressure: 980 items."},
                {"timestamp": "2026-09-03T08:42:10Z", "level": "WARN", "message": "Pod payment-service-7f89d-4kx2 marked Unhealthy by Kubelet livenessProbe: HTTP 503 response time > 5000ms."},
                {"timestamp": "2026-09-03T08:43:00Z", "level": "FATAL", "message": "Out of memory leak in connection retention pool v2.4.1. Immediate restart with memory flush or rolling restart required."}
            ],
            "auth-service": [
                {"timestamp": "2026-09-03T08:35:10Z", "level": "WARN", "message": "Redis connection pool reached 98% saturation (980/1000 conns)."},
                {"timestamp": "2026-09-03T08:36:22Z", "level": "ERROR", "message": "RedisTimeoutException: Cannot borrow connection from pool after 3000ms."},
                {"timestamp": "2026-09-03T08:37:05Z", "level": "ERROR", "message": "OAuth2 token verification failing for JWT signature cache miss. Falling back to DB sync."},
                {"timestamp": "2026-09-03T08:38:11Z", "level": "FATAL", "message": "Database thread pool starvation due to Redis cache bypass."}
            ],
            "order-service": [
                {"timestamp": "2026-09-03T08:42:30Z", "level": "WARN", "message": "Payment client returned HTTP 504 Gateway Timeout for order #98214."},
                {"timestamp": "2026-09-03T08:43:02Z", "level": "ERROR", "message": "Order checkout flow degraded due to upstream dependency payment-service latency spike."}
            ],
            "notification-service": [
                {"timestamp": "2026-09-03T08:40:00Z", "level": "INFO", "message": "Email delivery queue processed 420 messages with 0 errors."}
            ]
        }

        self.execution_audit_log: List[Dict[str, Any]] = []

    def get_service(self, service_name: str) -> Optional[Dict[str, Any]]:
        return self.services.get(service_name)

    def record_recovery(self, service_name: str, action: str) -> None:
        """
        Reflect a successful remediation in the service's log stream.

        Fatal/critical lines describing the pre-remediation failure are cleared
        and a recovery line is appended, so the post-action log health check
        observes the same reality as the post-action metrics. Without this the
        simulation would report healthy metrics alongside stale FATAL logs.
        """
        logs = self.logs_db.get(service_name)
        if logs is None:
            return

        self.logs_db[service_name] = [
            entry
            for entry in logs
            if entry.get("level") not in ("CRITICAL", "FATAL")
            and "OOMKilled" not in entry.get("message", "")
        ]
        self.logs_db[service_name].append(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "level": "INFO",
                "message": (
                    f"Remediation '{action}' completed for {service_name}. "
                    "Service reporting healthy steady state."
                ),
            }
        )


# Singleton cluster instance
CLUSTER = MockInfrastructureCluster()
