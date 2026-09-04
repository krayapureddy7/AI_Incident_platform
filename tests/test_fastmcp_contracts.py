"""
Contract and Policy Tests for the FastMCP tool server and Security Tool Gateway.

Validates typed tool contracts, FastMCP versioning, tenant/environment
isolation, deterministic policy boundaries, blast-radius gating, idempotency,
the agent permission matrix, and the guarantee that a rejected action never
reaches the tool layer.

Each test builds a private cluster/gateway stack so mutations performed by one
test are invisible to the others.
"""
import unittest

from app.tools.contracts import AGENT_TOOL_PERMISSIONS, MUTATING_TOOLS, READ_ONLY_TOOLS

from conftest import build_isolated_stack


class TestFastMcpToolContracts(unittest.TestCase):
    """Typed tool signatures, structured envelopes, and version metadata."""

    def setUp(self):
        self.cluster, self.impl, self.gateway = build_isolated_stack()

    def test_get_logs_contract(self):
        resp = self.impl.get_logs(
            service="payment-service", environment="prod", tenant="default", timeframe="15m"
        )
        self.assertTrue(resp["ok"])
        self.assertIn("logs", resp["data"])
        self.assertIn("total_lines_found", resp["data"])
        self.assertEqual(resp["metadata"]["tool"], "get_logs")
        self.assertEqual(resp["metadata"]["version"], "1.1.0")

    def test_get_metrics_contract(self):
        resp = self.impl.get_metrics(service="payment-service", environment="prod")
        self.assertTrue(resp["ok"])
        for field in ("memory_utilization_pct", "cpu_utilization_pct", "error_rate_pct"):
            self.assertIn(field, resp["data"])
        self.assertEqual(resp["metadata"]["version"], "1.0.0")

    def test_dependency_graph_contract(self):
        resp = self.impl.get_dependency_graph(service="payment-service", environment="prod")
        self.assertTrue(resp["ok"])
        self.assertIn("upstream_dependencies", resp["data"])
        self.assertIn("downstream_dependents", resp["data"])
        self.assertIn("tier", resp["data"])
        self.assertEqual(resp["metadata"]["version"], "1.0.0")

    def test_restart_contract(self):
        resp = self.impl.simulate_restart(
            service="payment-service", environment="prod", reason="Clear heap leak"
        )
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["data"]["status"], "SUCCESS")
        self.assertIn("post_action_metrics", resp["data"])
        self.assertEqual(resp["metadata"]["version"], "1.2.0")

    def test_scale_contract(self):
        resp = self.impl.simulate_scale(
            service="payment-service", replicas=5, environment="prod", reason="Absorb spike"
        )
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["data"]["current_replicas"], 5)
        self.assertEqual(resp["metadata"]["version"], "1.2.0")

    def test_invalid_arguments(self):
        resp = self.impl.get_logs(service="", environment="prod", timeframe="15m")
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "INVALID_ARGUMENT")

        resp_scale = self.impl.simulate_scale(
            service="payment-service", replicas=0, environment="prod"
        )
        self.assertFalse(resp_scale["ok"])
        self.assertEqual(resp_scale["error"]["code"], "INVALID_ARGUMENT")

    def test_error_envelopes_declare_retryability(self):
        resp = self.impl.get_metrics(service="ghost-service", environment="prod")
        self.assertFalse(resp["ok"])
        self.assertIn("retryable", resp["error"])
        self.assertFalse(resp["error"]["retryable"])

    def test_tenant_mismatch_at_tool_layer(self):
        resp = self.impl.get_logs(
            service="payment-service", environment="prod", tenant="rogue-tenant", timeframe="15m"
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "TENANT_MISMATCH")

    def test_mcp_round_trip_returns_structured_envelope(self):
        """A call through the MCP client must preserve the structured envelope."""
        resp = self.gateway.execute_read_only(
            "get_metrics", "payment-service", caller_role="investigator"
        )
        self.assertTrue(resp["ok"])
        self.assertIn("data", resp)
        self.assertIn("metadata", resp)
        self.assertEqual(resp["metadata"]["tool"], "get_metrics")


class TestGatewayPolicyBoundaries(unittest.TestCase):
    """Tenant, environment, policy, autonomy, and idempotency enforcement."""

    def setUp(self):
        self.cluster, self.impl, self.gateway = build_isolated_stack()

    def test_tenant_mismatch_at_gateway(self):
        resp = self.gateway.execute_proposal(
            proposal={
                "tool_name": "simulate_restart",
                "service": "payment-service",
                "environment": "prod",
                "tenant": "tenant-b",
                "parameters": {},
            },
            env_context="prod",
            tenant_context="tenant-a",
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "TENANT_MISMATCH")
        self.assertEqual(self.gateway.mutating_call_count, 0)

    def test_environment_mismatch_at_gateway(self):
        resp = self.gateway.execute_proposal(
            proposal={
                "tool_name": "simulate_restart",
                "service": "payment-service",
                "environment": "staging",
                "parameters": {},
            },
            env_context="prod",
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "ENVIRONMENT_MISMATCH")

    def test_policy_denied_on_excessive_scale(self):
        resp = self.gateway.execute_proposal(
            {
                "tool_name": "simulate_scale",
                "service": "payment-service",
                "environment": "prod",
                "tenant": "default",
                "parameters": {"replicas": 99},
            },
            env_context="prod",
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "POLICY_DENIED")

    def test_tier0_requires_human_approval(self):
        resp = self.gateway.execute_proposal(
            proposal={
                "tool_name": "simulate_restart",
                "service": "auth-service",
                "environment": "prod",
                "tenant": "default",
                "parameters": {"reason": "Restarting tier-0 core"},
            },
            env_context="prod",
            caller_role="orchestrator",
            human_approval_token=None,
        )
        self.assertFalse(resp["ok"])
        self.assertIn(resp["error"]["code"], ["AUTONOMY_LIMIT", "BLAST_RADIUS_EXCEEDED"])
        self.assertTrue(resp["error"]["details"]["requires_human_token"])

    def test_tier0_proceeds_with_valid_token(self):
        resp = self.gateway.execute_proposal(
            proposal={
                "tool_name": "simulate_restart",
                "service": "auth-service",
                "environment": "prod",
                "tenant": "default",
                "parameters": {"reason": "Approved tier-0 restart"},
            },
            env_context="prod",
            caller_role="orchestrator",
            human_approval_token="TOKEN-HUMAN-APPROVED-SRE-77",
        )
        self.assertTrue(resp["ok"])
        self.assertEqual(self.gateway.mutating_call_count, 1)

    def test_malformed_human_token_is_rejected(self):
        """A prefix-only token carries no approver identity and must not pass."""
        resp = self.gateway.execute_proposal(
            proposal={
                "tool_name": "simulate_restart",
                "service": "auth-service",
                "environment": "prod",
                "parameters": {"reason": "forged"},
            },
            env_context="prod",
            human_approval_token="TOKEN-HUMAN-APPROVED-",
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(self.gateway.mutating_call_count, 0)

    def test_idempotent_execution(self):
        proposal = {
            "tool_name": "simulate_restart",
            "service": "payment-service",
            "environment": "prod",
            "tenant": "default",
            "parameters": {"reason": "Idempotency test restart"},
        }
        first = self.gateway.execute_proposal(proposal, env_context="prod")
        self.assertTrue(first["ok"])

        second = self.gateway.execute_proposal(proposal, env_context="prod")
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"]["code"], "ALREADY_EXECUTED")
        self.assertIn("cached_result", second)
        # The duplicate must not have reached the tool layer.
        self.assertEqual(self.gateway.mutating_call_count, 1)
        self.assertEqual(len(self.cluster.execution_audit_log), 1)


class TestAgentPermissionMatrix(unittest.TestCase):
    """
    Regression coverage for privilege escalation.

    The permission matrix is the only thing standing between a read-only agent
    and a production mutation, so it is asserted directly and through both
    gateway entry points.
    """

    def setUp(self):
        self.cluster, self.impl, self.gateway = build_isolated_stack()

    def test_matrix_grants_no_tools_to_non_executing_agents(self):
        for role in ("planner", "ops", "verifier"):
            self.assertEqual(AGENT_TOOL_PERMISSIONS[role], frozenset(), role)

    def test_investigator_is_limited_to_read_only_tools(self):
        self.assertEqual(AGENT_TOOL_PERMISSIONS["investigator"], READ_ONLY_TOOLS)

    def test_planner_cannot_execute_any_tool(self):
        resp = self.gateway.execute_proposal(
            proposal={
                "tool_name": "simulate_restart",
                "service": "payment-service",
                "environment": "prod",
            },
            caller_role="planner",
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "AUTONOMY_LIMIT")
        self.assertEqual(self.gateway.fastmcp_call_count, 0)

    def test_investigator_cannot_mutate_via_proposal_path(self):
        for tool_name in sorted(MUTATING_TOOLS):
            with self.subTest(tool=tool_name):
                resp = self.gateway.execute_proposal(
                    proposal={
                        "tool_name": tool_name,
                        "service": "auth-service",
                        "environment": "prod",
                        "parameters": {"replicas": 5, "reason": "escalation attempt"},
                    },
                    caller_role="investigator",
                )
                self.assertFalse(resp["ok"])
                self.assertEqual(resp["error"]["code"], "POLICY_DENIED")

    def test_investigator_cannot_mutate_via_read_only_path(self):
        for tool_name in sorted(MUTATING_TOOLS):
            with self.subTest(tool=tool_name):
                resp = self.gateway.execute_read_only(
                    tool_name=tool_name, service="auth-service", caller_role="investigator"
                )
                self.assertFalse(resp["ok"])
                self.assertEqual(resp["error"]["code"], "POLICY_DENIED")

    def test_unknown_role_is_denied_by_default(self):
        resp = self.gateway.execute_proposal(
            proposal={
                "tool_name": "get_metrics",
                "service": "payment-service",
                "environment": "prod",
            },
            caller_role="attacker",
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "AUTONOMY_LIMIT")

    def test_escalation_attempts_leave_infrastructure_untouched(self):
        """No escalation attempt may reach the tool layer or mutate the cluster."""
        attempts = [
            ("planner", "simulate_restart"),
            ("investigator", "simulate_restart"),
            ("investigator", "simulate_scale"),
            ("ops", "simulate_restart"),
            ("verifier", "simulate_scale"),
            ("attacker", "simulate_restart"),
        ]
        for role, tool_name in attempts:
            resp = self.gateway.execute_proposal(
                proposal={
                    "tool_name": tool_name,
                    "service": "auth-service",
                    "environment": "prod",
                    "parameters": {"replicas": 6, "reason": "escalation"},
                },
                caller_role=role,
            )
            self.assertFalse(resp["ok"], f"{role} -> {tool_name} was not blocked")

        self.assertEqual(self.gateway.fastmcp_call_count, 0)
        self.assertEqual(self.gateway.mutating_call_count, 0)
        self.assertEqual(len(self.cluster.execution_audit_log), 0)
        self.assertEqual(self.cluster.get_service("auth-service")["status"], "DEGRADED")


class TestRejectedActionsNeverReachTools(unittest.TestCase):
    """
    Architectural safety verification.

    When an action is rejected on any ground -- role, schema, tenant,
    environment, policy, or autonomy -- the tool layer must never be invoked.
    """

    def setUp(self):
        self.cluster, self.impl, self.gateway = build_isolated_stack()

    def test_no_rejection_path_reaches_the_tool_layer(self):
        rejections = [
            (
                "unregistered tool",
                {"tool_name": "rm_rf", "service": "payment-service", "environment": "prod"},
                "orchestrator",
                None,
            ),
            (
                "cross-environment",
                {
                    "tool_name": "simulate_restart",
                    "service": "payment-service",
                    "environment": "staging",
                },
                "orchestrator",
                None,
            ),
            (
                "excessive scale",
                {
                    "tool_name": "simulate_scale",
                    "service": "payment-service",
                    "environment": "prod",
                    "parameters": {"replicas": 50},
                },
                "orchestrator",
                None,
            ),
            (
                "unapproved tier-0",
                {
                    "tool_name": "simulate_restart",
                    "service": "auth-service",
                    "environment": "prod",
                },
                "orchestrator",
                None,
            ),
            (
                "planner privilege",
                {
                    "tool_name": "simulate_restart",
                    "service": "payment-service",
                    "environment": "prod",
                },
                "planner",
                None,
            ),
            (
                "empty service",
                {"tool_name": "simulate_restart", "service": "", "environment": "prod"},
                "orchestrator",
                None,
            ),
        ]

        for label, proposal, role, token in rejections:
            with self.subTest(rejection=label):
                before = self.gateway.fastmcp_call_count
                resp = self.gateway.execute_proposal(
                    proposal, env_context="prod", caller_role=role, human_approval_token=token
                )
                self.assertFalse(resp["ok"], label)
                self.assertEqual(
                    self.gateway.fastmcp_call_count,
                    before,
                    f"tool layer was invoked despite rejection: {label}",
                )
                self.assertFalse(resp["metadata"]["fastmcp_invoked"])

        self.assertEqual(len(self.cluster.execution_audit_log), 0)


if __name__ == "__main__":
    unittest.main()
