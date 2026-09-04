"""
Integration tests ensuring the complete evaluation gate and invariants hold.
Validates:
1. A2A typed Pydantic envelope messaging
2. Persistence & resumability across LangGraph checkpoints
3. Safety boundary & FastMCP invocation prevention on rejected actions
4. Post-action verification with before/after telemetry & log comparison
5. Evaluation coverage: cross-tenant, cross-environment, blast-radius, unapproved Tier-3, and idempotency
6. Deterministic workflow control
"""
import unittest
from mini_platform.evals.eval_runner import EvaluationSuite
from mini_platform.models import Incident, AutonomyTier, AgentRole, A2AMessage
from mini_platform.orchestrator.state_machine import IncidentOrchestrator
from conftest import build_isolated_stack


class TestEvaluationGate(unittest.TestCase):
    def setUp(self):
        self.cluster, self.tool_server, self.tool_gateway = build_isolated_stack()
        self.orchestrator = IncidentOrchestrator(
            tool_server=self.tool_server,
            tool_gateway=self.tool_gateway,
        )

    def test_full_evaluation_suite(self):
        """Run the full evaluation benchmark suite; every scenario must pass."""
        suite = EvaluationSuite()
        report = suite.run_all()
        self.assertEqual(report["failed"], 0, f"Benchmark failed on scenarios: {[r for r in report['results'] if not r['passed']]}")
        self.assertEqual(report["passed"], report["total"])
        self.assertEqual(report["pass_rate_pct"], 100.0)

    def test_eval_cross_tenant_isolation(self):
        """Evaluation: Cross-tenant isolation invariant."""
        incident = Incident(
            id="INC-TENANT-EVAL",
            title="Cross-tenant access violation test",
            description="Agent in tenant-a context attempts to access tenant-b resources",
            service="payment-service",
            environment="prod",
            tenant="tenant-b"
        )
        res = self.orchestrator.run_incident(
            incident=incident,
            env_context="prod",
            tenant_context="tenant-a"
        )
        self.assertEqual(res["final_state"], "FAILED")
        self.assertEqual(res["workflow_status"], "REJECTED")
        violations = res["safety_result"]["policy_violations"]
        self.assertTrue(any("CROSS_TENANT_VIOLATION" in v for v in violations))

    def test_eval_cross_environment_isolation(self):
        """Evaluation: Cross-environment isolation invariant."""
        incident = Incident(
            id="INC-ENV-EVAL",
            title="Cross-environment access violation test",
            description="Agent in staging context attempts to run action on prod service",
            service="payment-service",
            environment="prod"
        )
        res = self.orchestrator.run_incident(
            incident=incident,
            env_context="staging"
        )
        self.assertEqual(res["final_state"], "FAILED")
        self.assertEqual(res["workflow_status"], "REJECTED")
        violations = res["safety_result"]["policy_violations"]
        self.assertTrue(any("CROSS_ENVIRONMENT_VIOLATION" in v for v in violations))

    def test_eval_blast_radius_governance(self):
        """Evaluation: Blast-radius traversal and tier classification invariant."""
        # auth-service is tier-0 with wide dependent blast radius
        incident = Incident(
            id="INC-BLAST-EVAL",
            title="Auth Service cascade risk",
            description="High blast radius service restart",
            service="auth-service",
            environment="prod"
        )
        res = self.orchestrator.run_incident(incident=incident, env_context="prod")
        self.assertEqual(res["final_state"], "AWAITING_APPROVAL")
        self.assertEqual(res["workflow_status"], "BLOCKED_FOR_APPROVAL")
        safety = res["safety_result"]
        self.assertEqual(safety["tier"], AutonomyTier.TIER_3_HUMAN_APPROVAL)
        self.assertTrue(safety["requires_human_token"])
        self.assertGreaterEqual(safety["blast_radius_analysis"]["total_affected_services"], 2)

    def test_eval_unapproved_tier3_rejection(self):
        """Evaluation: Unapproved Tier-3 mutation is held and never executed."""
        incident = Incident(
            id="INC-TIER3-UNAPPROVED",
            title="Tier-3 mutation without token",
            description="Mutation on auth-service without approval token",
            service="auth-service",
            environment="prod"
        )
        res = self.orchestrator.run_incident(incident=incident, human_approval_token=None)
        self.assertEqual(res["final_state"], "AWAITING_APPROVAL")
        self.assertIsNone(res.get("execution_result"))
        # The safety invariant: no mutating tool was ever dispatched. Read-only
        # dispatches are expected here, since evidence gathering also traverses
        # the gateway; they are counted separately from mutations.
        self.assertEqual(self.orchestrator.tool_gateway.mutating_call_count, 0)
        self.assertGreater(self.orchestrator.tool_gateway.fastmcp_call_count, 0)
        self.assertEqual(len(self.cluster.execution_audit_log), 0)

    def test_eval_duplicate_action_idempotency(self):
        """Evaluation: Duplicate mutating action blocked with ALREADY_EXECUTED."""
        proposal = {
            "tool_name": "simulate_restart",
            "service": "payment-service",
            "environment": "prod",
            "tenant": "default",
            "parameters": {"reason": "Idempotent evaluation"}
        }
        resp1 = self.orchestrator.tool_gateway.execute_proposal(proposal, env_context="prod")
        self.assertTrue(resp1["ok"])

        resp2 = self.orchestrator.tool_gateway.execute_proposal(proposal, env_context="prod")
        self.assertFalse(resp2["ok"])
        self.assertEqual(resp2["error"]["code"], "ALREADY_EXECUTED")

    def test_persistence_and_resumption_workflow(self):
        """
        Persistence: LangGraph checkpoints are persisted and resumable.
        1. Run incident requiring human approval (Tier-3) -> stops at AWAITING_APPROVAL.
        2. Checkpoint state is verified in MemorySaver.
        3. Resume incident with valid human approval token -> executes to COMPLETED.
        """
        run_id = "RESUME-TEST-RUN-001"
        incident = Incident(
            id="INC-PERSIST-01",
            title="Core Auth Timeout (Requires Approval)",
            description="Auth service thread pool exhaustion requiring human signoff",
            service="auth-service",
            environment="prod"
        )

        # 1. Initial run without token -> halts at AWAITING_APPROVAL
        res1 = self.orchestrator.run_incident(incident=incident, run_id=run_id, human_approval_token=None)
        self.assertEqual(res1["final_state"], "AWAITING_APPROVAL")
        self.assertEqual(res1["workflow_status"], "BLOCKED_FOR_APPROVAL")

        # 2. Inspect persisted LangGraph checkpoint
        checkpoint = self.orchestrator.get_checkpoint_state(run_id)
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint["values"]["current_state"], "AWAITING_APPROVAL")
        self.assertEqual(checkpoint["values"]["run_id"], run_id)

        # 3. Resume workflow with cryptographic human approval token
        valid_token = "TOKEN-HUMAN-APPROVED-SR_SRE_991"
        res2 = self.orchestrator.resume_incident(run_id=run_id, human_approval_token=valid_token)
        self.assertEqual(res2["final_state"], "COMPLETED")
        self.assertEqual(res2["workflow_status"], "RESOLVED")
        self.assertIsNotNone(res2["execution_result"])
        self.assertTrue(res2["execution_result"]["ok"])

    def test_a2a_typed_pydantic_envelopes(self):
        """
        A2A: Verify that all inter-agent communications pass through typed Pydantic A2AMessage envelopes.
        """
        incident = Incident(
            id="INC-A2A-TEST",
            title="A2A Envelope Verification",
            description="Ensure message envelopes are validated Pydantic models",
            service="payment-service",
            environment="prod"
        )
        res = self.orchestrator.run_incident(incident=incident)
        self.assertEqual(res["final_state"], "COMPLETED")

        # Verify a2a messages were recorded as typed envelopes
        a2a_history = res.get("a2a_messages", [])
        self.assertGreaterEqual(len(a2a_history), 4)

        for raw_msg in a2a_history:
            # Validate every single message against Pydantic schema
            msg_model = A2AMessage.model_validate(raw_msg)
            self.assertIn(msg_model.sender, [AgentRole.ORCHESTRATOR, AgentRole.PLANNER, AgentRole.INVESTIGATOR, AgentRole.OPS, AgentRole.VERIFIER])
            self.assertIn(msg_model.recipient, [AgentRole.ORCHESTRATOR, AgentRole.PLANNER, AgentRole.INVESTIGATOR, AgentRole.OPS, AgentRole.VERIFIER])
            self.assertIsInstance(msg_model.payload, dict)
            self.assertTrue(msg_model.timestamp.endswith("Z"))

    def test_post_action_verification_before_after_comparison(self):
        """
        Post-action verification: Ensure remediation validates before/after telemetry and logs.
        """
        incident = Incident(
            id="INC-POST-VERIFY",
            title="Payment Heap Exhaustion Verification",
            description="payment-service memory at 96% with OOM errors",
            service="payment-service",
            environment="prod"
        )
        res = self.orchestrator.run_incident(incident=incident)
        self.assertEqual(res["final_state"], "COMPLETED")

        # Verify before/after comparison artifacts
        rec_verif = res.get("recovery_verification")
        self.assertIsNotNone(rec_verif)
        self.assertTrue(rec_verif["recovered"])
        self.assertIn("status_comparison", rec_verif)
        self.assertIn("metric_deltas", rec_verif)
        self.assertIn("log_health", rec_verif)
        # Memory utilization must have dropped after remediation
        mem_reduction = rec_verif["metric_deltas"]["memory_utilization"]["reduction_pct"]
        self.assertGreater(mem_reduction, 0)


if __name__ == "__main__":
    unittest.main()

