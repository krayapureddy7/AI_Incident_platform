"""
Unit and integration tests for LangGraph incident orchestration.
Validates LangGraph StateGraph compilation, typed nodes, and Pydantic structured I/O.
"""
import unittest
from pydantic import ValidationError
from mini_platform.models import Incident, WorkflowState
from mini_platform.agents.schemas import (
    PlannerInput, PlannerOutput
)
from mini_platform.orchestrator.state_machine import IncidentOrchestrator
from mini_platform.orchestrator.langgraph_workflow import build_incident_graph
from conftest import build_isolated_stack


class TestLangGraphOrchestration(unittest.TestCase):
    def setUp(self):
        self.cluster, self.tool_server, self.tool_gateway = build_isolated_stack()
        self.orchestrator = IncidentOrchestrator(
            tool_server=self.tool_server,
            tool_gateway=self.tool_gateway,
        )
        self.incident = Incident(
            id="INC-LG-01",
            title="Payment Out of Memory Degradation",
            description="payment-service OOMKilled in prod, memory saturation 95%",
            service="payment-service",
            environment="prod",
            severity="SEV-1"
        )

    def test_graph_compilation(self):
        """Verify LangGraph StateGraph builds and compiles with all nodes and transitions."""
        graph = build_incident_graph(self.orchestrator)
        self.assertIsNotNone(graph)
        # Verify graph contains expected node keys
        self.assertIn("planner", graph.nodes)
        self.assertIn("investigator", graph.nodes)
        self.assertIn("ops", graph.nodes)
        self.assertIn("verifier", graph.nodes)
        self.assertIn("executor", graph.nodes)
        self.assertIn("hold_for_approval", graph.nodes)
        self.assertIn("policy_rejected", graph.nodes)

    def test_planner_node_structured_io(self):
        """Verify PlannerAgent.run_node validates Pydantic input and returns typed PlannerOutput."""
        planner = self.orchestrator.planner
        state = {
            "incident": self.incident.to_dict(),
            "tracer": None
        }
        res = planner.run_node(state)
        self.assertIn("plan", res)
        self.assertEqual(res["current_state"], "PLANNING")
        # Validate through Pydantic model
        output_model = PlannerOutput(**res["plan"])
        self.assertEqual(len(output_model.symptoms), 1)
        self.assertEqual(output_model.symptoms[0], "MEMORY_EXHAUSTION_OR_LEAK")
        self.assertEqual(len(output_model.delegated_tasks), 4)

    def test_planner_input_schema_validation_error(self):
        """Verify PlannerInput enforces required fields."""
        with self.assertRaises(ValidationError):
            # Missing required fields id, title, description
            PlannerInput(service="payment-service")

    def test_investigator_node_structured_io(self):
        """Verify InvestigatorAgent.run_node executes and produces validated InvestigatorOutput."""
        planner = self.orchestrator.planner
        investigator = self.orchestrator.investigator

        plan_res = planner.run_node({"incident": self.incident.to_dict(), "tracer": None})
        state = {
            "incident": self.incident.to_dict(),
            "plan": plan_res["plan"],
            "tracer": None
        }
        res = investigator.run_node(state)
        self.assertIn("evidence", res)
        self.assertEqual(res["current_state"], "INVESTIGATING")
        evidence = res["evidence"]
        self.assertEqual(evidence["identified_root_cause"], "MEMORY_LEAK_HEAP_EXHAUSTION")
        self.assertGreaterEqual(len(evidence["citations"]), 1)

    def test_ops_node_structured_io(self):
        """Verify OpsAgent.run_node produces validated ActionProposal."""
        planner = self.orchestrator.planner
        investigator = self.orchestrator.investigator
        ops = self.orchestrator.ops

        plan_res = planner.run_node({"incident": self.incident.to_dict(), "tracer": None})
        inv_res = investigator.run_node({"incident": self.incident.to_dict(), "plan": plan_res["plan"], "tracer": None})

        state = {
            "incident": self.incident.to_dict(),
            "evidence": inv_res["evidence"],
            "tracer": None
        }
        res = ops.run_node(state)
        self.assertIn("proposal", res)
        self.assertEqual(res["current_state"], "PROPOSING_ACTION")
        proposal = res["proposal"]
        self.assertEqual(proposal["tool_name"], "simulate_restart")
        self.assertEqual(proposal["service"], "payment-service")
        self.assertEqual(proposal["autonomy_tier"], 2)

    def test_verifier_node_structured_io(self):
        """Verify VerifierAgent.run_node audits safety and returns SafetyCheckResult."""
        verifier = self.orchestrator.verifier
        proposal = {
            "action_id": "ACT-TEST-01",
            "tool_name": "simulate_restart",
            "service": "payment-service",
            "environment": "prod",
            "parameters": {"service": "payment-service", "reason": "Test rolling restart"},
            "reasoning": "Memory leak fix",
            "evidence_citations": [{"doc_id": "DOC-RB-PAY-001"}],
            "estimated_blast_radius": 2,
            "autonomy_tier": 2,
            "rejected_alternatives": []
        }
        state = {
            "proposal": proposal,
            "env_context": "prod",
            "human_approval_token": None,
            "tracer": None
        }
        res = verifier.run_node(state)
        self.assertIn("safety_result", res)
        self.assertEqual(res["current_state"], "SAFETY_VERIFICATION")
        safety_result = res["safety_result"]
        self.assertTrue(safety_result["approved"])
        self.assertFalse(safety_result["requires_human_token"])

    def test_full_incident_graph_resolution(self):
        """Verify end-to-end incident execution through LangGraph StateGraph completes successfully."""
        result = self.orchestrator.run_incident(
            incident=self.incident,
            env_context="prod"
        )
        self.assertEqual(result["status"], "RESOLVED")
        self.assertEqual(result["final_state"], WorkflowState.COMPLETED.value)
        self.assertEqual(result["proposal"]["tool_name"], "simulate_restart")
        self.assertIn("trace", result)
        self.assertIn("replay_metadata", result)
        self.assertTrue(result["execution_result"]["ok"])


if __name__ == "__main__":
    unittest.main()
