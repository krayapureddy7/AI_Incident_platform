"""
Knowledge corpus containing operational runbooks, architecture documents, and past postmortems.
"""

from typing import List, Dict, Any

#: Version of the knowledge corpus contents.
__version__ = "1.0.0"

KNOWLEDGE_DOCUMENTS: List[Dict[str, Any]] = [
    {
        "id": "DOC-RB-PAY-001",
        "title": "Runbook: Payment Service Memory Leak & OOM Remediation",
        "service": "payment-service",
        "env": "prod",
        "type": "runbook",
        "version": "2.4.0",
        "content": (
            "Payment Service Out Of Memory (OOM) and Heap Saturation Runbook. "
            "Symptoms: Memory utilization > 90%, frequent full GC pauses, p99 latency spiking > 3000ms, "
            "Kubelet restarting pods with exit code 137 (OOMKilled), 503 Service Unavailable errors. "
            "Root Cause: Known memory leak in PaymentSessionPool connection retention pool (v2.4.1). "
            "Immediate Remediation: "
            "1. Execute rolling restart of payment-service pods to purge leaking retained buffer pools. "
            "2. Verify dependent services (order-service, mobile-api-gateway) recover normal latency. "
            "3. If latency persists after restart, temporarily scale replicas from 3 to 5 to distribute load. "
            "Safety Warning: Do not scale down during active degradation. "
            "Escalation: If rolling restart fails, page checkout-eng@corp.internal."
        ),
        "tags": ["payment-service", "memory", "oom", "heap", "restart", "rolling-restart", "latency"]
    },
    {
        "id": "DOC-RB-AUTH-001",
        "title": "Runbook: Auth Service Redis Connection Exhaustion & Pool Starvation",
        "service": "auth-service",
        "env": "prod",
        "type": "runbook",
        "version": "3.1.0",
        "content": (
            "Auth Service Connection Exhaustion and Redis Timeout Runbook. "
            "Symptoms: CPU > 90%, RedisTimeoutException in logs, JWT verification falling back to database, "
            "database connection pool saturation, 504 gateway timeouts across dependent services. "
            "Dependencies: redis-auth-cluster, user-db. Direct dependents: payment-service, order-service. "
            "Blast Radius: Tier-0 critical service. High blast radius. "
            "Remediation Procedure: "
            "1. Scale auth-service replicas from 4 to 6 to absorb connection burst. "
            "2. Flush idle connection pool or restart redis-auth sentinel nodes if unresponsive. "
            "Safety Warning: Auth-service is Tier-0. Direct restart without scaling causes authentication blackout for payment and order flows. Tier-3 human authorization required for restart."
        ),
        "tags": ["auth-service", "redis", "connection", "pool", "scale", "tier-0", "blast-radius"]
    },
    {
        "id": "DOC-RB-ORD-001",
        "title": "Runbook: Order Service Cascade Failure & Upstream Circuit Breakers",
        "service": "order-service",
        "env": "prod",
        "type": "runbook",
        "version": "1.9.0",
        "content": (
            "Order Service Cascading Latency & Upstream Dependency Triage. "
            "Symptoms: Order creation timeouts, CircuitBreaker(payment-service) open, order queue backlog. "
            "Analysis: Order service degradation is typically a symptom rather than root cause. "
            "Check upstream payment-service health and p99 latency before restarting order service. "
            "Remediation: Do not restart order service first; remediate upstream payment-service. "
            "Once payment service health is restored, check order service queue drain rates."
        ),
        "tags": ["order-service", "cascade", "circuit-breaker", "upstream", "payment-service"]
    },
    {
        "id": "DOC-ARCH-PAY-001",
        "title": "Architecture Spec: Checkout & Payment Service Topology",
        "service": "payment-service",
        "env": "prod",
        "type": "architecture",
        "version": "2.4.0",
        "content": (
            "Payment Service Architecture and Dependency Topography. "
            "Payment service processes credit card authorization and tokenized transactions. "
            "It depends on auth-service for JWT validation and notification-service for receipt webhooks. "
            "Downstream dependents include order-service and mobile-api-gateway. "
            "SLA: 99.95% uptime, p99 latency < 250ms. Current tier: Tier-1 service."
        ),
        "tags": ["payment-service", "architecture", "dependencies", "sla"]
    },
    {
        "id": "DOC-PM-2025-11",
        "title": "Postmortem: Incident 2025-11-14 Payment Gateway Heap Collapse",
        "service": "payment-service",
        "env": "prod",
        "type": "postmortem",
        "version": "1.0.0",
        "content": (
            "Postmortem: Major incident on Nov 14 2025 where payment-service suffered heap exhaustion under 3x traffic. "
            "Action Taken: Engineers performed a rolling restart of payment-service, dropping memory from 98% to 35%, "
            "and immediately scaled pods from 3 to 6. "
            "Resolution time: 8 minutes. "
            "Key Learning: Rolling restart is safe and effective because zero downtime is incurred with standard Kubernetes readiness probes."
        ),
        "tags": ["postmortem", "payment-service", "heap", "rolling-restart", "traffic"]
    }
]
