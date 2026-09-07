"""
Tools package providing FastMCP Tool Server, FastMCP Client, and Security Tool Gateway.

Exports are resolved lazily (PEP 562). ``gateway`` imports the safety engine,
which in turn imports ``app.tools.contracts`` for the shared tool contract
constants -- so eagerly importing ``gateway`` here made the package init part of
a cycle, and ``import mini_platform.safety.guardrails`` failed unless something
had already imported ``app.tools`` first. Deferring the submodule import to
first attribute access breaks that cycle while keeping ``from app.tools import
ToolGateway`` working exactly as before.
"""
from typing import Any

_EXPORTS = {
    "fastmcp_server": ".server",
    "get_tool_metadata": ".server",
    "TOOL_VERSIONS": ".server",
    "ToolClient": ".client",
    "ToolGateway": ".gateway",
    "GatewayExecutionError": ".gateway",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Import the owning submodule on first access to one of its exports."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value  # Cache, so later lookups skip this hook entirely.
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(_EXPORTS))
