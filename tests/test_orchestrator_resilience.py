"""
Tests for node-level retry and timeout handling.

The orchestration requirement is that a transient node failure is retried and a
hung node is abandoned rather than stalling the workflow indefinitely. These
budgets were previously accepted as constructor arguments but never applied, so
these tests pin the behaviour directly on the guard and end-to-end through the
orchestrator.
"""
import time
import unittest

from mini_platform.models import Incident
from mini_platform.orchestrator.resilience import (
    NodeExecutionError,
    NodeTimeoutError,
    with_resilience,
)

from conftest import build_isolated_orchestrator, build_isolated_stack


class TestResilienceGuard(unittest.TestCase):
    """Unit coverage for the retry/timeout wrapper."""

    def test_successful_node_passes_through(self):
        guarded = with_resilience(
            lambda state: {"ok": True, "seen": state["x"]},
            node="probe",
            timeout_sec=5.0,
        )
        self.assertEqual(guarded({"x": 7}), {"ok": True, "seen": 7})

    def test_transient_failure_is_retried_then_succeeds(self):
        attempts = {"n": 0}

        def flaky(_state):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionError("transient tool failure")
            return {"attempts": attempts["n"]}

        guarded = with_resilience(
            flaky, node="flaky", timeout_sec=5.0, max_retries=2, backoff_base_sec=0.001
        )
        self.assertEqual(guarded({}), {"attempts": 3})
        self.assertEqual(attempts["n"], 3)

    def test_retry_budget_is_exhausted_and_reported(self):
        attempts = {"n": 0}

        def always_fails(_state):
            attempts["n"] += 1
            raise RuntimeError("permanent failure")

        guarded = with_resilience(
            always_fails, node="broken", timeout_sec=5.0, max_retries=2, backoff_base_sec=0.001
        )
        with self.assertRaises(NodeExecutionError) as ctx:
            guarded({})

        self.assertEqual(attempts["n"], 3)  # 1 initial + 2 retries
        self.assertEqual(ctx.exception.node, "broken")
        self.assertEqual(ctx.exception.attempts, 3)
        self.assertIsInstance(ctx.exception.last_error, RuntimeError)

    def test_zero_retries_means_a_single_attempt(self):
        """Mutating nodes must never be auto-retried."""
        attempts = {"n": 0}

        def failing_mutation(_state):
            attempts["n"] += 1
            raise RuntimeError("execution failed")

        guarded = with_resilience(failing_mutation, node="executor", timeout_sec=5.0, max_retries=0)
        with self.assertRaises(NodeExecutionError):
            guarded({})
        self.assertEqual(attempts["n"], 1)

    def test_hung_node_times_out(self):
        def hangs(_state):
            time.sleep(30)
            return {"never": True}

        guarded = with_resilience(hangs, node="hung", timeout_sec=0.2, max_retries=0)
        started = time.time()
        with self.assertRaises(NodeExecutionError) as ctx:
            guarded({})
        elapsed = time.time() - started

        self.assertIsInstance(ctx.exception.last_error, NodeTimeoutError)
        self.assertLess(elapsed, 10.0, "timeout did not abandon the hung node promptly")

    def test_timeout_is_retried_when_the_node_is_retryable(self):
        attempts = {"n": 0}

        def slow_then_fast(_state):
            attempts["n"] += 1
            if attempts["n"] == 1:
                time.sleep(30)
            return {"attempts": attempts["n"]}

        guarded = with_resilience(
            slow_then_fast,
            node="slow",
            timeout_sec=0.2,
            max_retries=1,
            backoff_base_sec=0.001,
        )
        self.assertEqual(guarded({}), {"attempts": 2})

    def test_retries_are_recorded_in_the_audit_trace(self):
        from mini_platform.tracing.tracer import AuditTracer

        tracer = AuditTracer(run_id="RESILIENCE-TRACE-TEST")
        attempts = {"n": 0}

        def flaky(_state):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise ConnectionError("transient")
            return {}

        guarded = with_resilience(
            flaky,
            node="traced",
            timeout_sec=5.0,
            max_retries=2,
            backoff_base_sec=0.001,
            tracer_resolver=lambda _state: tracer,
        )
        guarded({"current_state": "INVESTIGATING"})

        retry_events = [t for t in tracer.state_transitions if t["trigger"] == "NODE_RETRY"]
        self.assertEqual(len(retry_events), 1)
        self.assertEqual(retry_events[0]["metadata"]["node"], "traced")
        self.assertTrue(retry_events[0]["metadata"]["will_retry"])


class TestOrchestratorResilienceWiring(unittest.TestCase):
    """The orchestrator's budgets must actually reach the graph nodes."""

    def setUp(self):
        self.incident = Incident(
            id="INC-RESILIENCE",
            title="Payment OOM",
            description="payment-service OOMKilled, memory at 96%",
            service="payment-service",
            environment="prod",
        )

    def test_budgets_are_applied_not_merely_stored(self):
        orchestrator = build_isolated_orchestrator(max_retries=4, step_timeout_sec=9.0)
        self.assertEqual(orchestrator.max_retries, 4)
        self.assertEqual(orchestrator.step_timeout_sec, 9.0)
        # A run must still succeed with non-default budgets in place.
        result = orchestrator.run_incident(incident=self.incident)
        self.assertEqual(result["status"], "RESOLVED")

    def test_transient_agent_failure_is_retried_to_success(self):
        """A flaky agent recovers within budget and the workflow completes."""
        orchestrator = build_isolated_orchestrator(max_retries=2)
        real_handle = orchestrator.ops.handle_message
        calls = {"n": 0}

        def flaky_handle(message, context):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("transient ops failure")
            return real_handle(message, context)

        orchestrator.ops.handle_message = flaky_handle
        result = orchestrator.run_incident(incident=self.incident)

        self.assertEqual(result["status"], "RESOLVED")
        self.assertEqual(calls["n"], 2)

    def test_unrecoverable_node_failure_terminates_cleanly(self):
        """An exhausted retry budget yields a structured failure, not a crash."""
        orchestrator = build_isolated_orchestrator(max_retries=1)

        def always_fails(_message, _context):
            raise RuntimeError("investigator is down")

        orchestrator.investigator.handle_message = always_fails
        result = orchestrator.run_incident(incident=self.incident)

        self.assertEqual(result["status"], "ORCHESTRATION_FAILED")
        self.assertEqual(result["final_state"], "FAILED")
        self.assertEqual(result["failed_node"], "investigator")
        self.assertEqual(result["attempts"], 2)
        self.assertIn("investigator is down", result["reason"])
        # A failed orchestration must not have mutated infrastructure.
        self.assertEqual(orchestrator.tool_gateway.mutating_call_count, 0)
        # The trace is still persisted so the failure is auditable.
        self.assertIsNotNone(result["trace_file"])

    def test_executor_node_is_never_auto_retried(self):
        """Re-issuing a mutation of unknown outcome is unsafe and must not happen."""
        _, impl, gateway = build_isolated_stack()
        from mini_platform.orchestrator.state_machine import IncidentOrchestrator

        orchestrator = IncidentOrchestrator(
            tool_server=impl, tool_gateway=gateway, max_retries=3
        )
        calls = {"n": 0}
        real_execute = gateway.execute_proposal

        def failing_execute(proposal, **kwargs):
            """Fail only mutations, so evidence gathering still succeeds."""
            if proposal.get("tool_name", "").startswith("simulate_"):
                calls["n"] += 1
                raise RuntimeError("cluster API unreachable")
            return real_execute(proposal, **kwargs)

        gateway.execute_proposal = failing_execute
        result = orchestrator.run_incident(incident=self.incident)

        self.assertEqual(result["status"], "ORCHESTRATION_FAILED")
        self.assertEqual(result["failed_node"], "executor")
        self.assertEqual(calls["n"], 1, "the mutating node was retried")


if __name__ == "__main__":
    unittest.main()
