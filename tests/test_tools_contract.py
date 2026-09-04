"""
Contract and Schema Tests for the tool catalog and gateway dispatch path.

Verifies the published JSON-Schema contracts, parameter validation, environment
scoping, and structured error envelopes. These assertions previously targeted a
second, unauthenticated tool server that has since been removed; they now run
against the single surviving implementation reached through the Tool Gateway.
"""
import unittest

from app.tools.contracts import (
    MAX_REPLICA_CEILING,
    MUTATING_TOOLS,
    READ_ONLY_TOOLS,
    TOOL_VERSIONS,
)
from mini_platform.tools.mcp_server import TOOL_REGISTRY_METADATA

from conftest import build_isolated_stack


class TestToolCatalogContracts(unittest.TestCase):
    """The published catalog must be complete, versioned, and self-describing."""

    def setUp(self):
        self.cluster, self.impl, self.gateway = build_isolated_stack()

    def test_catalog_schema_conformance(self):
        catalog = self.impl.tool_catalog()
        self.assertEqual(len(catalog), 5)
        for tool in catalog:
            self.assertIn("name", tool)
            self.assertIn("version", tool)
            self.assertIn("description", tool)
            self.assertIn("parameters", tool)
            self.assertEqual(tool["parameters"]["type"], "object")
            self.assertIn("required", tool["parameters"])
            # A tool is exactly one of read-only or mutating.
            self.assertNotEqual(tool["read_only"], tool["mutating"])

    def test_catalog_versions_match_registry(self):
        catalog = {tool["name"]: tool["version"] for tool in self.impl.tool_catalog()}
        self.assertEqual(catalog, dict(TOOL_VERSIONS))

    def test_permission_classes_partition_the_catalog(self):
        self.assertEqual(READ_ONLY_TOOLS | MUTATING_TOOLS, set(TOOL_VERSIONS))
        self.assertFalse(READ_ONLY_TOOLS & MUTATING_TOOLS)

    def test_registry_metadata_declares_fastmcp(self):
        self.assertEqual(TOOL_REGISTRY_METADATA["protocol"], "FastMCP")
        self.assertEqual(TOOL_REGISTRY_METADATA["server_name"], "incident-tools")


class TestToolDispatchContracts(unittest.TestCase):
    """Tool behaviour and error envelopes via the gateway dispatch path."""

    def setUp(self):
        self.cluster, self.impl, self.gateway = build_isolated_stack()

    def _read(self, tool_name, service, **kwargs):
        return self.gateway.execute_read_only(
            tool_name=tool_name, service=service, caller_role="investigator", **kwargs
        )

    def test_get_logs_success(self):
        resp = self._read("get_logs", "payment-service", extra_params={"timeframe": "15m"})
        self.assertTrue(resp["ok"])
        self.assertIn("logs", resp["data"])
        self.assertGreater(len(resp["data"]["logs"]), 0)
        self.assertEqual(resp["metadata"]["version"], TOOL_VERSIONS["get_logs"])
        self.assertTrue(resp["metadata"]["fastmcp_invoked"])

    def test_get_metrics_success(self):
        resp = self._read("get_metrics", "payment-service")
        self.assertTrue(resp["ok"])
        self.assertIn("memory_utilization_pct", resp["data"])
        self.assertIn("p99_latency_ms", resp["data"])

    def test_dependency_graph_success(self):
        resp = self._read("get_dependency_graph", "payment-service")
        self.assertTrue(resp["ok"])
        self.assertIn("upstream_dependencies", resp["data"])
        self.assertIn("downstream_dependents", resp["data"])
        self.assertEqual(resp["data"]["tier"], "tier-1")

    def test_unregistered_tool_envelope(self):
        resp = self.gateway.execute_proposal(
            {"tool_name": "drop_database", "service": "payment-service", "environment": "prod"},
            env_context="prod",
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "TOOL_NOT_FOUND")
        self.assertEqual(self.gateway.fastmcp_call_count, 0)

    def test_missing_service_envelope(self):
        resp = self.gateway.execute_proposal(
            {"tool_name": "get_metrics", "service": "", "environment": "prod"},
            env_context="prod",
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "INVALID_ARGUMENT")

    def test_cross_environment_isolation(self):
        resp = self.gateway.execute_proposal(
            {
                "tool_name": "simulate_restart",
                "service": "payment-service",
                "environment": "staging",
                "parameters": {"reason": "Test reboot"},
            },
            env_context="prod",
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "ENVIRONMENT_MISMATCH")
        self.assertEqual(self.gateway.mutating_call_count, 0)

    def test_nonexistent_service_error_envelope(self):
        resp = self._read("get_metrics", "non-existent-service")
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "SERVICE_NOT_FOUND")
        self.assertIn("available_services", resp["error"]["details"])

    def test_scale_ceiling_guard(self):
        resp = self.gateway.execute_proposal(
            {
                "tool_name": "simulate_scale",
                "service": "payment-service",
                "environment": "prod",
                "parameters": {"replicas": 99, "reason": "over-scale test"},
            },
            env_context="prod",
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "POLICY_DENIED")
        self.assertEqual(self.gateway.mutating_call_count, 0)

    def test_scale_to_zero_rejected(self):
        resp = self.gateway.execute_proposal(
            {
                "tool_name": "simulate_scale",
                "service": "payment-service",
                "environment": "prod",
                "parameters": {"replicas": 0, "reason": "downscale to zero"},
            },
            env_context="prod",
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "POLICY_DENIED")

    def test_service_ceiling_is_stricter_of_two_limits(self):
        """payment-service declares max_replicas=12; the platform cap of 10 wins."""
        self.assertGreater(self.cluster.get_service("payment-service")["max_replicas"], MAX_REPLICA_CEILING)
        resp = self.impl.simulate_scale(
            service="payment-service", replicas=MAX_REPLICA_CEILING + 1, environment="prod"
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "SCALE_CEILING_EXCEEDED")
        self.assertEqual(resp["error"]["details"]["max_replicas"], MAX_REPLICA_CEILING)


class TestClusterIsolation(unittest.TestCase):
    """Each stack owns its cluster; mutations must not leak between them."""

    def test_mutation_does_not_leak_across_stacks(self):
        cluster_a, _, gateway_a = build_isolated_stack()
        cluster_b, _, _ = build_isolated_stack()

        resp = gateway_a.execute_proposal(
            {
                "tool_name": "simulate_restart",
                "service": "payment-service",
                "environment": "prod",
                "parameters": {"reason": "isolation probe"},
            },
            env_context="prod",
        )
        self.assertTrue(resp["ok"])

        self.assertEqual(cluster_a.get_service("payment-service")["status"], "HEALTHY")
        self.assertEqual(cluster_b.get_service("payment-service")["status"], "DEGRADED")
        self.assertEqual(len(cluster_a.execution_audit_log), 1)
        self.assertEqual(len(cluster_b.execution_audit_log), 0)


if __name__ == "__main__":
    unittest.main()
