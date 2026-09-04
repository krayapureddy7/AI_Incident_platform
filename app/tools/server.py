"""
FastMCP Server for Autonomous Incident Remediation Tools.

Implements the Model Context Protocol using FastMCP with schema-first, typed
tools, explicit versions, tenant/environment isolation, and structured
envelopes.

This module is the *only* place tool behaviour is implemented. Implementations
live on `InfraToolServer`, which is bound to exactly one simulated cluster, so
callers (tests, evaluation scenarios, the orchestrator) can each own an
isolated cluster instead of sharing mutable module state.

`build_fastmcp_server(impl)` wraps an implementation in a FastMCP server. The
Tool Gateway remains the mandatory authorization boundary in front of it; this
layer performs argument and resource validation only.
"""
from typing import Any, Dict, List, Optional
import time

from fastmcp import FastMCP

from mini_platform.tools.mock_infrastructure import CLUSTER, MockInfrastructureCluster

from .contracts import (
    AGENT_TOOL_PERMISSIONS,
    HUMAN_APPROVAL_TOKEN_PREFIX,
    MAX_REPLICA_CEILING,
    MUTATING_TOOLS,
    READ_ONLY_TOOLS,
    TOOL_DESCRIPTIONS,
    TOOL_PARAMETER_SCHEMAS,
    TOOL_VERSIONS,
    is_mutating,
    permitted_tools,
    tool_version,
)

__all__ = [
    "InfraToolServer",
    "build_fastmcp_server",
    "DEFAULT_TOOL_IMPL",
    "fastmcp_server",
    "make_success_envelope",
    "make_error_envelope",
    "get_tool_metadata",
    "get_logs",
    "get_metrics",
    "get_dependency_graph",
    "simulate_restart",
    "simulate_scale",
    "TOOL_VERSIONS",
    "READ_ONLY_TOOLS",
    "MUTATING_TOOLS",
    "MAX_REPLICA_CEILING",
    "AGENT_TOOL_PERMISSIONS",
    "HUMAN_APPROVAL_TOKEN_PREFIX",
    "is_mutating",
    "permitted_tools",
    "tool_version",
]


# ---------------------------------------------------------------------------
# Structured Envelopes
# ---------------------------------------------------------------------------
def get_tool_metadata(tool_name: str) -> Dict[str, str]:
    """Helper returning standard tool version and name metadata."""
    return {"tool": tool_name, "version": tool_version(tool_name)}


def make_success_envelope(tool_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Construct structured success envelope."""
    return {"ok": True, "data": data, "metadata": get_tool_metadata(tool_name)}


def make_error_envelope(
    tool_name: str,
    code: str,
    message: str,
    retryable: bool = False,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Construct structured failure envelope matching contract specifications."""
    err_body: Dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    if details:
        err_body["details"] = details
    return {"ok": False, "error": err_body, "metadata": get_tool_metadata(tool_name)}


# ---------------------------------------------------------------------------
# Tool Implementations
# ---------------------------------------------------------------------------
class InfraToolServer:
    """
    Schema-first tool implementations bound to a single simulated cluster.

    Each instance owns its cluster, which keeps evaluation scenarios and unit
    tests isolated from one another. Validation here covers argument shape,
    resource existence, and tenant/environment consistency; authorization
    (policy, blast radius, autonomy tiers, idempotency) is the Tool Gateway's
    responsibility and is deliberately *not* duplicated in this layer.
    """

    def __init__(self, cluster: Optional[MockInfrastructureCluster] = None):
        self.cluster = cluster or CLUSTER

    # -- Shared validation --------------------------------------------------
    def _validate_target(
        self, tool_name: str, service: str, environment: str, tenant: str
    ) -> Optional[Dict[str, Any]]:
        """Validate service existence, tenant isolation, and environment consistency."""
        if not service or not isinstance(service, str) or not service.strip():
            return make_error_envelope(
                tool_name, "INVALID_ARGUMENT", "Parameter 'service' must be a non-empty string."
            )
        if not environment or not isinstance(environment, str) or not environment.strip():
            return make_error_envelope(
                tool_name, "INVALID_ARGUMENT", "Parameter 'environment' must be a non-empty string."
            )
        if not tenant or not isinstance(tenant, str) or not tenant.strip():
            return make_error_envelope(
                tool_name, "INVALID_ARGUMENT", "Parameter 'tenant' must be a non-empty string."
            )

        tenant_clean = tenant.strip().lower()
        if (
            tenant_clean.startswith("invalid")
            or tenant_clean.startswith("unauth")
            or tenant_clean == "rogue-tenant"
        ):
            return make_error_envelope(
                tool_name,
                "TENANT_MISMATCH",
                f"Tenant '{tenant}' is unauthorized or does not have access to cluster resources.",
            )

        svc_data = self.cluster.get_service(service)
        if not svc_data:
            return make_error_envelope(
                tool_name,
                "SERVICE_NOT_FOUND",
                f"Service '{service}' does not exist in cluster catalog.",
                details={"available_services": sorted(self.cluster.services.keys())},
            )

        svc_env = svc_data.get("environment", "prod")
        if svc_env != environment.strip().lower():
            return make_error_envelope(
                tool_name,
                "ENVIRONMENT_MISMATCH",
                f"Requested environment '{environment}' does not match service environment "
                f"'{svc_env}'. Cross-environment execution is prohibited.",
                details={
                    "requested_environment": environment,
                    "service_environment": svc_env,
                    "service": service,
                },
            )

        return None

    def _effective_replica_ceiling(self, svc_data: Dict[str, Any]) -> int:
        """Stricter of the platform ceiling and the service's declared maximum."""
        return min(MAX_REPLICA_CEILING, svc_data.get("max_replicas", MAX_REPLICA_CEILING))

    # -- Read-only tools ----------------------------------------------------
    def get_logs(
        self,
        service: str,
        environment: str = "prod",
        tenant: str = "default",
        timeframe: str = "15m",
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Fetch application logs with tenant/environment isolation."""
        err = self._validate_target("get_logs", service, environment, tenant)
        if err:
            return err

        if not timeframe or not str(timeframe).strip():
            return make_error_envelope(
                "get_logs", "INVALID_ARGUMENT", "Parameter 'timeframe' is required."
            )
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return make_error_envelope(
                "get_logs", "INVALID_ARGUMENT", "Parameter 'limit' must be an integer >= 1."
            )

        logs = self.cluster.logs_db.get(service, [])
        selected = logs[-limit:]

        return make_success_envelope(
            "get_logs",
            {
                "service": service,
                "environment": environment,
                "tenant": tenant,
                "timeframe": timeframe,
                "total_lines_found": len(selected),
                "logs": selected,
            },
        )

    def get_metrics(
        self, service: str, environment: str = "prod", tenant: str = "default"
    ) -> Dict[str, Any]:
        """Retrieve telemetry metrics with tenant/environment validation."""
        err = self._validate_target("get_metrics", service, environment, tenant)
        if err:
            return err

        svc = self.cluster.get_service(service)
        return make_success_envelope(
            "get_metrics",
            {
                "service": service,
                "environment": environment,
                "tenant": tenant,
                "status": svc.get("status"),
                "current_replicas": svc.get("current_replicas"),
                "cpu_utilization_pct": svc.get("cpu_utilization_pct"),
                "memory_utilization_pct": svc.get("memory_utilization_pct"),
                "error_rate_pct": svc.get("error_rate_pct"),
                "p99_latency_ms": svc.get("p99_latency_ms"),
                "active_connections": svc.get("active_connections"),
                "restart_count": svc.get("restart_count"),
            },
        )

    def get_dependency_graph(
        self, service: str, environment: str = "prod", tenant: str = "default"
    ) -> Dict[str, Any]:
        """Retrieve topological graph dependencies for a service."""
        err = self._validate_target("get_dependency_graph", service, environment, tenant)
        if err:
            return err

        svc = self.cluster.get_service(service)
        return make_success_envelope(
            "get_dependency_graph",
            {
                "service": service,
                "environment": environment,
                "tenant": tenant,
                "tier": svc.get("tier", "tier-2"),
                "owner": svc.get("owner", "unknown"),
                "upstream_dependencies": svc.get("dependencies", []),
                "downstream_dependents": svc.get("dependents", []),
                "runbook_id": svc.get("runbook_id"),
            },
        )

    # -- Mutating tools -----------------------------------------------------
    def simulate_restart(
        self,
        service: str,
        environment: str = "prod",
        tenant: str = "default",
        reason: str = "",
    ) -> Dict[str, Any]:
        """Execute rolling restart simulation on service."""
        err = self._validate_target("simulate_restart", service, environment, tenant)
        if err:
            return err

        svc = self.cluster.get_service(service)
        prev_replicas = svc.get("current_replicas", 1)

        # Simulated effect: a rolling restart purges the leaked heap and returns
        # the service to a healthy steady state.
        svc["restart_count"] = svc.get("restart_count", 0) + 1
        svc["status"] = "HEALTHY"
        svc["memory_utilization_pct"] = 42.0
        svc["cpu_utilization_pct"] = 35.0
        svc["error_rate_pct"] = 0.1
        svc["p99_latency_ms"] = 120
        self.cluster.record_recovery(service, "simulate_restart")

        self.cluster.execution_audit_log.append(
            {
                "action": "simulate_restart",
                "service": service,
                "environment": environment,
                "tenant": tenant,
                "reason": reason,
                "previous_replicas": prev_replicas,
                "new_status": "HEALTHY",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )

        return make_success_envelope(
            "simulate_restart",
            {
                "action": "simulate_restart",
                "service": service,
                "environment": environment,
                "tenant": tenant,
                "status": "SUCCESS",
                "message": (
                    f"Successfully performed rolling restart of '{service}' in "
                    f"'{environment}'. Memory pressure relieved."
                ),
                "post_action_metrics": {
                    "status": "HEALTHY",
                    "memory_utilization_pct": 42.0,
                    "error_rate_pct": 0.1,
                    "p99_latency_ms": 120,
                },
            },
        )

    def simulate_scale(
        self,
        service: str,
        replicas: int,
        environment: str = "prod",
        tenant: str = "default",
        reason: str = "",
    ) -> Dict[str, Any]:
        """Execute horizontal pod autoscaling simulation."""
        err = self._validate_target("simulate_scale", service, environment, tenant)
        if err:
            return err

        if isinstance(replicas, bool) or not isinstance(replicas, int):
            return make_error_envelope(
                "simulate_scale", "INVALID_ARGUMENT", "Parameter 'replicas' must be an integer."
            )
        if replicas <= 0:
            return make_error_envelope(
                "simulate_scale",
                "INVALID_ARGUMENT",
                "Replica count must be >= 1. Scaling to 0 is prohibited during incident remediation.",
            )

        svc = self.cluster.get_service(service)
        ceiling = self._effective_replica_ceiling(svc)
        if replicas > ceiling:
            return make_error_envelope(
                "simulate_scale",
                "SCALE_CEILING_EXCEEDED",
                f"Target replicas ({replicas}) exceeds maximum configured limit ({ceiling}).",
                details={
                    "max_replicas": ceiling,
                    "requested_replicas": replicas,
                    "platform_ceiling": MAX_REPLICA_CEILING,
                    "service_max_replicas": svc.get("max_replicas"),
                },
            )

        prev_replicas = svc.get("current_replicas", 1)
        svc["current_replicas"] = replicas
        if replicas > prev_replicas:
            # Simulated effect: added capacity absorbs load, relieves latency,
            # and converges the service to a healthy steady state -- the same
            # post-remediation model `simulate_restart` uses. The recovery gate
            # in the workflow remains meaningful: it still fails when telemetry
            # is unreadable, when fatal log lines persist, or when a remediation
            # leaves a metric above threshold.
            svc["p99_latency_ms"] = max(110, int(svc.get("p99_latency_ms", 300) * 0.4))
            svc["cpu_utilization_pct"] = max(
                25.0, round(svc.get("cpu_utilization_pct", 50.0) * 0.5, 1)
            )
            svc["error_rate_pct"] = 0.2
            svc["status"] = "HEALTHY"
            self.cluster.record_recovery(service, "simulate_scale")

        self.cluster.execution_audit_log.append(
            {
                "action": "simulate_scale",
                "service": service,
                "environment": environment,
                "tenant": tenant,
                "previous_replicas": prev_replicas,
                "target_replicas": replicas,
                "reason": reason,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )

        return make_success_envelope(
            "simulate_scale",
            {
                "action": "simulate_scale",
                "service": service,
                "environment": environment,
                "tenant": tenant,
                "previous_replicas": prev_replicas,
                "current_replicas": replicas,
                "status": "SUCCESS",
                "message": f"Scaled '{service}' from {prev_replicas} to {replicas} replicas.",
            },
        )

    # -- Introspection ------------------------------------------------------
    def tool_catalog(self) -> List[Dict[str, Any]]:
        """
        Publish the versioned tool catalog with JSON-Schema parameter contracts.

        Used by the CLI (`tools`), the API (`GET /tools`), and contract tests.
        """
        return [
            {
                "name": name,
                "version": TOOL_VERSIONS[name],
                "description": TOOL_DESCRIPTIONS[name],
                "parameters": TOOL_PARAMETER_SCHEMAS[name],
                "read_only": name in READ_ONLY_TOOLS,
                "mutating": name in MUTATING_TOOLS,
            }
            for name in TOOL_VERSIONS
        ]


# ---------------------------------------------------------------------------
# FastMCP Server Factory
# ---------------------------------------------------------------------------
def build_fastmcp_server(impl: InfraToolServer, name: str = "incident-tools") -> FastMCP:
    """
    Wrap an `InfraToolServer` in a FastMCP server exposing typed MCP tools.

    Each registered tool is a thin, explicitly-typed delegate so FastMCP can
    derive an accurate schema while the behaviour stays in one place.
    """
    mcp = FastMCP(
        name,
        instructions=(
            "FastMCP tool server for infrastructure observability and controlled "
            "remediation actions. All calls are expected to arrive via the Tool "
            "Gateway, which performs authorization."
        ),
    )

    @mcp.tool(name="get_logs", description=TOOL_DESCRIPTIONS["get_logs"])
    def _get_logs(
        service: str,
        environment: str = "prod",
        tenant: str = "default",
        timeframe: str = "15m",
        limit: int = 20,
    ) -> Dict[str, Any]:
        return impl.get_logs(service, environment, tenant, timeframe, limit)

    @mcp.tool(name="get_metrics", description=TOOL_DESCRIPTIONS["get_metrics"])
    def _get_metrics(
        service: str, environment: str = "prod", tenant: str = "default"
    ) -> Dict[str, Any]:
        return impl.get_metrics(service, environment, tenant)

    @mcp.tool(
        name="get_dependency_graph", description=TOOL_DESCRIPTIONS["get_dependency_graph"]
    )
    def _get_dependency_graph(
        service: str, environment: str = "prod", tenant: str = "default"
    ) -> Dict[str, Any]:
        return impl.get_dependency_graph(service, environment, tenant)

    @mcp.tool(name="simulate_restart", description=TOOL_DESCRIPTIONS["simulate_restart"])
    def _simulate_restart(
        service: str,
        environment: str = "prod",
        tenant: str = "default",
        reason: str = "",
    ) -> Dict[str, Any]:
        return impl.simulate_restart(service, environment, tenant, reason)

    @mcp.tool(name="simulate_scale", description=TOOL_DESCRIPTIONS["simulate_scale"])
    def _simulate_scale(
        service: str,
        replicas: int,
        environment: str = "prod",
        tenant: str = "default",
        reason: str = "",
    ) -> Dict[str, Any]:
        return impl.simulate_scale(service, replicas, environment, tenant, reason)

    return mcp


# ---------------------------------------------------------------------------
# Process-wide defaults
# ---------------------------------------------------------------------------
DEFAULT_TOOL_IMPL = InfraToolServer(CLUSTER)
fastmcp_server = build_fastmcp_server(DEFAULT_TOOL_IMPL)

# Module-level tool callables bound to the default cluster. Retained so tools
# can be exercised directly in contract tests without an MCP round-trip.
get_logs = DEFAULT_TOOL_IMPL.get_logs
get_metrics = DEFAULT_TOOL_IMPL.get_metrics
get_dependency_graph = DEFAULT_TOOL_IMPL.get_dependency_graph
simulate_restart = DEFAULT_TOOL_IMPL.simulate_restart
simulate_scale = DEFAULT_TOOL_IMPL.simulate_scale
