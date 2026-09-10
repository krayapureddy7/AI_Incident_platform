"""
Unit Tests for Multi-Agent Behaviors and Structured A2A Messaging.
"""
import unittest
from mini_platform.models import Incident, AgentRole, MessageType, A2AMessage
from mini_platform.agents.planner import PlannerAgent
from mini_platform.agents.investigator import InvestigatorAgent
from mini_platform.agents.ops import OpsAgent
from mini_platform.agents.verifier import VerifierAgent
from mini_platform.tracing.tracer import AuditTracer

from conftest import build_isolated_stack


class TestAgentWorkflows(unittest.TestCase):
    def setUp(self):
        self.incident = Incident(
            id="TEST-INC-1",
            title="Payment Heap Memory Alert",
            description="payment-service OOMKilled in production, memory leak suspected",
            service="payment-service",
            environment="prod"
        )
        self.tracer = AuditTracer(run_id="TEST-RUN-001")
        self.cluster, self.tool_impl, self.gateway = build_isolated_stack()
        self.context = {
            "incident": self.incident,
            "tracer": self.tracer,
            "env_context": "prod",
            "tenant_context": "default",
        }

    def test_planner_agent_decomposition(self):
        planner = PlannerAgent()
        msg = A2AMessage(
            sender=AgentRole.ORCHESTRATOR,
            recipient=AgentRole.PLANNER,
            message_type=MessageType.TASK_DELEGATION,
            correlation_id="TEST-CORR-1",
            payload={"incident": self.incident.to_dict()}
        )
        reply = planner.handle_message(msg, self.context)
        self.assertEqual(reply.sender, AgentRole.PLANNER)
        self.assertEqual(reply.recipient, AgentRole.INVESTIGATOR)
        self.assertEqual(reply.message_type, MessageType.TASK_DELEGATION)
        self.assertIn("MEMORY_EXHAUSTION_OR_LEAK", reply.payload["symptoms"])
        self.assertGreaterEqual(len(reply.payload["delegated_tasks"]), 3)

    def test_investigator_agent_evidence_synthesis(self):
        planner = PlannerAgent()
        planner_reply = planner.handle_message(
            A2AMessage(
                sender=AgentRole.ORCHESTRATOR,
                recipient=AgentRole.PLANNER,
                message_type=MessageType.TASK_DELEGATION,
                correlation_id="TEST-CORR-2",
                payload={"incident": self.incident.to_dict()}
            ),
            self.context
        )
        investigator = InvestigatorAgent(tool_gateway=self.gateway)
        inv_reply = investigator.handle_message(planner_reply, self.context)
        self.assertEqual(inv_reply.recipient, AgentRole.OPS)
        self.assertEqual(inv_reply.message_type, MessageType.EVIDENCE_REPORT)
        evidence = inv_reply.payload["evidence"]
        self.assertEqual(evidence["identified_root_cause"], "MEMORY_LEAK_HEAP_EXHAUSTION")
        self.assertGreater(len(evidence["citations"]), 0)
        # Reported symptoms (memory/leak keywords) corroborate the telemetry-derived
        # root cause here, so the summary carries no corroboration caveat at all.
        self.assertNotIn("corroboration", evidence["diagnosis_summary"].lower())

    def test_investigator_flags_uncorroborated_diagnosis_without_changing_it(self):
        """
        A description with no matching keywords (e.g. a Kafka-lag alert) on
        payment-service still yields MEMORY_LEAK_HEAP_EXHAUSTION -- the static
        telemetry never varies with incident text, and telemetry is the only
        signal this decides on. What must change is that the mismatch between
        the reported symptom(s) and the evidence is now visible, both in the
        summary text and as a distinct decision in the audit trace.
        """
        kafka_incident = Incident(
            id="TEST-INC-KAFKA",
            title="Kafka Consumer Lag Alert",
            description="Kafka consumer lag high, payment processing delayed",
            service="payment-service",
            environment="prod",
        )
        planner = PlannerAgent()
        planner_reply = planner.handle_message(
            A2AMessage(
                sender=AgentRole.ORCHESTRATOR,
                recipient=AgentRole.PLANNER,
                message_type=MessageType.TASK_DELEGATION,
                correlation_id="TEST-CORR-KAFKA",
                payload={"incident": kafka_incident.to_dict()},
            ),
            self.context,
        )
        self.assertEqual(planner_reply.payload["symptoms"], ["UNKNOWN_DEGRADATION"])

        investigator = InvestigatorAgent(tool_gateway=self.gateway)
        inv_reply = investigator.handle_message(planner_reply, self.context)
        evidence = inv_reply.payload["evidence"]

        # Root cause stays evidence-driven -- unchanged from the OOM case above.
        self.assertEqual(evidence["identified_root_cause"], "MEMORY_LEAK_HEAP_EXHAUSTION")
        # But the mismatch is now visible rather than silent.
        self.assertIn("UNCORROBORATED", evidence["diagnosis_summary"])

        step = next(
            s for s in self.tracer.steps if s.step_id == "STEP-INVESTIGATOR-01"
        )
        self.assertTrue(
            any("Symptom/evidence corroboration" in d for d in step.decisions)
        )

    def test_ops_agent_action_proposal(self):
        # Synthetic evidence report, so the Ops assertions do not depend on the
        # Investigator's tool round-trip.
        inv_reply = A2AMessage(
            sender=AgentRole.INVESTIGATOR,
            recipient=AgentRole.OPS,
            message_type=MessageType.EVIDENCE_REPORT,
            correlation_id="TEST-CORR-3",
            payload={
                "incident": self.incident.to_dict(),
                "evidence": {
                    "identified_root_cause": "MEMORY_LEAK_HEAP_EXHAUSTION",
                    "citations": [{"doc_id": "DOC-RB-PAY-001"}],
                    "metrics": {"memory_utilization_pct": 96.0}
                }
            }
        )
        ops = OpsAgent()
        ops_reply = ops.handle_message(inv_reply, self.context)
        self.assertEqual(ops_reply.recipient, AgentRole.VERIFIER)
        self.assertEqual(ops_reply.message_type, MessageType.ACTION_PROPOSAL)
        prop = ops_reply.payload["proposal"]
        self.assertEqual(prop["tool_name"], "simulate_restart")
        self.assertGreater(len(prop["rejected_alternatives"]), 0)

    def test_verifier_agent_autonomy_enforcement(self):
        verifier = VerifierAgent()
        proposal_dict = {
            "action_id": "ACT-TEST-1",
            "tool_name": "simulate_restart",
            "service": "payment-service",
            "environment": "prod",
            "parameters": {"service": "payment-service", "reason": "flush heap"},
            "reasoning": "runbook guidance",
            "evidence_citations": [],
            "estimated_blast_radius": 2,
            "autonomy_tier": 2
        }
        msg = A2AMessage(
            sender=AgentRole.OPS,
            recipient=AgentRole.VERIFIER,
            message_type=MessageType.ACTION_PROPOSAL,
            correlation_id="TEST-CORR-4",
            payload={"proposal": proposal_dict}
        )
        verifier_reply = verifier.handle_message(msg, self.context)
        self.assertEqual(verifier_reply.recipient, AgentRole.ORCHESTRATOR)
        safety_res = verifier_reply.payload["safety_result"]
        self.assertTrue(safety_res["approved"])
        self.assertEqual(safety_res["tier"], 2)


if __name__ == "__main__":
    unittest.main()
