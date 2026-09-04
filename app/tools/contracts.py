"""
Shared tool contract constants and JSON-Schema parameter definitions.

This module is the single source of truth for tool names, versions, permission
classes, and safety ceilings. Every other component (FastMCP server, Tool
Gateway, Safety Guardrails, agents, CLI) imports from here so that a contract
change lands in exactly one place.
"""
from typing import Any, Dict, FrozenSet

# --- Tool Version Registry -------------------------------------------------
TOOL_VERSIONS: Dict[str, str] = {
    "get_logs": "1.1.0",
    "get_metrics": "1.0.0",
    "get_dependency_graph": "1.0.0",
    "simulate_restart": "1.2.0",
    "simulate_scale": "1.2.0",
}

# --- Permission Classes ----------------------------------------------------
# Read-only tools observe state; mutating tools change it and therefore always
# require Verifier sign-off before the Tool Gateway will dispatch them.
READ_ONLY_TOOLS: FrozenSet[str] = frozenset(
    {"get_logs", "get_metrics", "get_dependency_graph"}
)
MUTATING_TOOLS: FrozenSet[str] = frozenset({"simulate_restart", "simulate_scale"})

# --- Agent Tool Permission Matrix ------------------------------------------
# Enforced by the Tool Gateway. An agent role absent from this map has no tool
# privileges at all (deny-by-default).
AGENT_TOOL_PERMISSIONS: Dict[str, FrozenSet[str]] = {
    "planner": frozenset(),
    "investigator": READ_ONLY_TOOLS,
    "ops": frozenset(),
    "verifier": frozenset(),
    "orchestrator": READ_ONLY_TOOLS | MUTATING_TOOLS,
}

# --- Safety Ceilings -------------------------------------------------------
# Platform-wide replica ceiling. Individual services may declare a lower
# `max_replicas`; the effective limit is always the stricter of the two.
MAX_REPLICA_CEILING: int = 10

# --- Human Approval --------------------------------------------------------
HUMAN_APPROVAL_TOKEN_PREFIX: str = "TOKEN-HUMAN-APPROVED-"

# --- Structured Error Codes ------------------------------------------------
ERROR_CODES: FrozenSet[str] = frozenset({
    "INVALID_ARGUMENT",
    "MISSING_REQUIRED_PARAMETER",
    "TENANT_MISMATCH",
    "ENVIRONMENT_MISMATCH",
    "POLICY_DENIED",
    "BLAST_RADIUS_EXCEEDED",
    "AUTONOMY_LIMIT",
    "SERVICE_NOT_FOUND",
    "TOOL_NOT_FOUND",
    "SCALE_CEILING_EXCEEDED",
    "TIMEOUT",
    "RATE_LIMITED",
    "ALREADY_EXECUTED",
    "INTERNAL_ERROR",
})


def tool_version(tool_name: str) -> str:
    """Resolve the registered version for a tool, defaulting to 1.0.0."""
    return TOOL_VERSIONS.get(tool_name, "1.0.0")


def is_mutating(tool_name: str) -> bool:
    """True when the tool changes infrastructure state."""
    return tool_name in MUTATING_TOOLS


def permitted_tools(caller_role: str) -> FrozenSet[str]:
    """Tools a caller role may invoke. Unknown roles receive no privileges."""
    return AGENT_TOOL_PERMISSIONS.get(caller_role, frozenset())


# --- JSON-Schema Parameter Contracts ---------------------------------------
# Published by the tool catalog so callers can introspect contracts without
# importing the implementations.
TOOL_PARAMETER_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "get_logs": {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service name (e.g. payment-service)"},
            "environment": {"type": "string", "description": "Environment scope", "default": "prod"},
            "tenant": {"type": "string", "description": "Tenant scope", "default": "default"},
            "timeframe": {"type": "string", "description": "Time window (e.g. '15m', '1h')", "default": "15m"},
            "limit": {"type": "integer", "description": "Max log lines to return", "default": 20},
        },
        "required": ["service"],
    },
    "get_metrics": {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service name"},
            "environment": {"type": "string", "description": "Environment scope", "default": "prod"},
            "tenant": {"type": "string", "description": "Tenant scope", "default": "default"},
        },
        "required": ["service"],
    },
    "get_dependency_graph": {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service name"},
            "environment": {"type": "string", "description": "Environment scope", "default": "prod"},
            "tenant": {"type": "string", "description": "Tenant scope", "default": "default"},
        },
        "required": ["service"],
    },
    "simulate_restart": {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service name"},
            "environment": {"type": "string", "description": "Environment scope", "default": "prod"},
            "tenant": {"type": "string", "description": "Tenant scope", "default": "default"},
            "reason": {"type": "string", "description": "Justification for the restart", "default": ""},
        },
        "required": ["service"],
    },
    "simulate_scale": {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service name"},
            "replicas": {
                "type": "integer",
                "description": f"Target replica count (min 1, max {MAX_REPLICA_CEILING})",
                "minimum": 1,
                "maximum": MAX_REPLICA_CEILING,
            },
            "environment": {"type": "string", "description": "Environment scope", "default": "prod"},
            "tenant": {"type": "string", "description": "Tenant scope", "default": "default"},
            "reason": {"type": "string", "description": "Justification for scaling", "default": ""},
        },
        "required": ["service", "replicas"],
    },
}

TOOL_DESCRIPTIONS: Dict[str, str] = {
    "get_logs": "Fetch redacted application and system logs for a target service within a specified timeframe.",
    "get_metrics": "Retrieve CPU, memory, error rates, p99 latency, and active connection metrics for a service.",
    "get_dependency_graph": "Retrieve upstream dependencies, downstream dependents, owner, and service tier.",
    "simulate_restart": "Execute a controlled rolling restart of all pods/instances of the service.",
    "simulate_scale": "Scale replica count for a target service to handle load or relieve degraded pods.",
}
