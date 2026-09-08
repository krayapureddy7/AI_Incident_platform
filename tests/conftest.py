"""
Shared test fixtures and isolation helpers.

Every helper here builds a *private* cluster, tool server, gateway, and
orchestrator. Nothing touches the process-wide default instances, so tests
cannot influence one another through mutable module state and may run in any
order or in parallel.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

import pytest

from app.tools.client import ToolClient
from app.tools.gateway import ToolGateway
from app.tools.server import InfraToolServer, build_fastmcp_server
from mini_platform.orchestrator.state_machine import IncidentOrchestrator
from mini_platform.persistence.store import IncidentStore
from mini_platform.tools.mock_infrastructure import MockInfrastructureCluster


def build_isolated_stack() -> Tuple[MockInfrastructureCluster, InfraToolServer, ToolGateway]:
    """
    Build a cluster, tool implementation, and gateway wired to one another.

    Returns the triple so a test can assert on cluster state directly while
    still exercising the full gateway -> client -> FastMCP -> tool path.
    """
    cluster = MockInfrastructureCluster()
    impl = InfraToolServer(cluster=cluster)
    gateway = ToolGateway(client=ToolClient(build_fastmcp_server(impl)))
    return cluster, impl, gateway


def build_isolated_orchestrator(
    store: Optional[IncidentStore] = None,
    max_retries: int = 2,
    step_timeout_sec: float = 15.0,
    llm_provider: Optional[Any] = None,
) -> IncidentOrchestrator:
    """Build an orchestrator over a private cluster and gateway."""
    _, impl, gateway = build_isolated_stack()
    return IncidentOrchestrator(
        tool_server=impl,
        tool_gateway=gateway,
        max_retries=max_retries,
        step_timeout_sec=step_timeout_sec,
        store=store,
        # Left as None the orchestrator resolves a provider from the
        # environment, so the suite runs deterministically by default and
        # exercises the LLM path when CI sets LLM_PROVIDER=mock. Tests that need
        # a specific generation pass a ScriptedProvider explicitly.
        llm_provider=llm_provider,
    )


@pytest.fixture
def isolated_stack():
    """Fixture yielding `(cluster, tool_impl, gateway)` bound together."""
    return build_isolated_stack()


@pytest.fixture
def cluster(isolated_stack) -> MockInfrastructureCluster:
    """A private simulated cluster."""
    return isolated_stack[0]


@pytest.fixture
def tool_impl(isolated_stack) -> InfraToolServer:
    """A tool implementation bound to the private cluster."""
    return isolated_stack[1]


@pytest.fixture
def gateway(isolated_stack) -> ToolGateway:
    """A Tool Gateway fronting the private cluster."""
    return isolated_stack[2]


@pytest.fixture
def orchestrator(isolated_stack) -> IncidentOrchestrator:
    """An orchestrator wired to the private cluster and gateway."""
    _, impl, gw = isolated_stack
    return IncidentOrchestrator(tool_server=impl, tool_gateway=gw)


@pytest.fixture
def store(tmp_path) -> IncidentStore:
    """An `IncidentStore` backed by a per-test temporary SQLite file."""
    return IncidentStore(db_path=str(tmp_path / "test_incidents.db"))
