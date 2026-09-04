"""
Tool server re-export for the `mini_platform` package.

The canonical implementation lives in `app.tools.server`. This module exists so
platform code can import tool primitives without reaching across packages, and
mirrors the re-export style already used by `mini_platform.tools.gateway`.

Historically this module carried a second, parallel tool-server implementation
(`McpToolServer`) with its own cluster and no authorization checks, which let
read-only agents reach mutating tools. That duplicate has been removed; there is
now exactly one tool implementation and one authorization boundary.
"""
from app.tools.contracts import (
    AGENT_TOOL_PERMISSIONS,
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
from app.tools.server import (
    DEFAULT_TOOL_IMPL,
    InfraToolServer,
    build_fastmcp_server,
    fastmcp_server,
    make_error_envelope,
    make_success_envelope,
)

# Registry metadata published by the CLI and API.
TOOL_REGISTRY_METADATA = {
    "version": "2.0.0",
    "protocol": "FastMCP",
    "server_name": "incident-tools",
}

# Process-wide default tool implementation.
TOOL_SERVER = DEFAULT_TOOL_IMPL

__all__ = [
    "InfraToolServer",
    "build_fastmcp_server",
    "fastmcp_server",
    "DEFAULT_TOOL_IMPL",
    "TOOL_SERVER",
    "TOOL_REGISTRY_METADATA",
    "make_success_envelope",
    "make_error_envelope",
    "TOOL_VERSIONS",
    "TOOL_DESCRIPTIONS",
    "TOOL_PARAMETER_SCHEMAS",
    "READ_ONLY_TOOLS",
    "MUTATING_TOOLS",
    "MAX_REPLICA_CEILING",
    "AGENT_TOOL_PERMISSIONS",
    "is_mutating",
    "permitted_tools",
    "tool_version",
]
