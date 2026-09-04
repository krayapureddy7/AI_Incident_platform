"""
Tests for durable incident and trace persistence.

The orchestration requirement is that workflow runs are persisted so they remain
replayable. These tests assert that a run recorded by one process-level object is
readable by a fresh one against the same database file -- i.e. durability, not
just in-memory bookkeeping.
"""
import unittest

from mini_platform.models import Incident
from mini_platform.persistence.store import IncidentStore
from mini_platform.tracing.tracer import TraceReplayer

from conftest import build_isolated_orchestrator


class TestIncidentStore(unittest.TestCase):
    """Direct coverage of the SQLite-backed store."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = f"{self._tmp.name}/incidents.db"
        self.store = IncidentStore(db_path=self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _incident(self, **overrides):
        base = {
            "id": "INC-STORE-1",
            "title": "Payment OOM",
            "service": "payment-service",
            "environment": "prod",
            "tenant": "default",
            "severity": "SEV-1",
        }
        base.update(overrides)
        return base

    def test_schema_is_created_on_construction(self):
        self.assertEqual(self.store.count_runs(), 0)

    def test_record_and_read_back_a_run(self):
        self.store.record_run(
            "RUN-1",
            self._incident(),
            {
                "status": "RESOLVED",
                "final_state": "COMPLETED",
                "proposal": {"tool_name": "simulate_restart"},
                "safety_result": {"approved": True, "tier": 2},
                "execution_result": {"ok": True},
            },
        )
        run = self.store.get_run("RUN-1")
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "RESOLVED")
        self.assertEqual(run["final_state"], "COMPLETED")
        self.assertEqual(run["proposal"]["tool_name"], "simulate_restart")
        self.assertTrue(run["safety_result"]["approved"])

    def test_unknown_run_returns_none(self):
        self.assertIsNone(self.store.get_run("RUN-DOES-NOT-EXIST"))
        self.assertIsNone(self.store.get_trace("RUN-DOES-NOT-EXIST"))

    def test_record_run_is_idempotent_upsert(self):
        """A Tier-3 hold followed by an approved resume yields one row."""
        incident = self._incident()
        self.store.record_run(
            "RUN-2", incident, {"status": "BLOCKED_FOR_APPROVAL", "final_state": "AWAITING_APPROVAL"}
        )
        self.store.record_run(
            "RUN-2", incident, {"status": "RESOLVED", "final_state": "COMPLETED"}
        )
        self.assertEqual(self.store.count_runs(), 1)
        self.assertEqual(self.store.get_run("RUN-2")["status"], "RESOLVED")

    def test_trace_round_trip(self):
        trace = {
            "run_id": "RUN-3",
            "step_count": 4,
            "total_tokens": 1650,
            "total_cost_usd": 0.0033,
            "state_transitions": [],
            "steps": [],
            "a2a_messages": [],
        }
        self.store.record_run("RUN-3", self._incident(), {"status": "RESOLVED", "final_state": "COMPLETED"})
        self.store.record_trace("RUN-3", trace)

        loaded = self.store.get_trace("RUN-3")
        self.assertEqual(loaded["step_count"], 4)
        self.assertEqual(loaded["total_tokens"], 1650)

    def test_list_runs_filters_and_orders(self):
        for idx, (service, status) in enumerate(
            [
                ("payment-service", "RESOLVED"),
                ("auth-service", "BLOCKED_FOR_APPROVAL"),
                ("payment-service", "REJECTED"),
            ]
        ):
            self.store.record_run(
                f"RUN-L{idx}",
                self._incident(service=service),
                {"status": status, "final_state": "X"},
            )

        payment = self.store.list_runs(service="payment-service")
        self.assertEqual(len(payment), 2)
        self.assertTrue(all(r["service"] == "payment-service" for r in payment))

        rejected = self.store.list_runs(status="REJECTED")
        self.assertEqual(len(rejected), 1)

        self.assertEqual(len(self.store.list_runs(limit=2)), 2)

    def test_durability_across_store_instances(self):
        """A second store over the same file sees the first store's writes."""
        self.store.record_run(
            "RUN-DURABLE", self._incident(), {"status": "RESOLVED", "final_state": "COMPLETED"}
        )
        self.store.record_trace("RUN-DURABLE", {"run_id": "RUN-DURABLE", "step_count": 2})

        reopened = IncidentStore(db_path=self.db_path)
        self.assertEqual(reopened.count_runs(), 1)
        self.assertEqual(reopened.get_run("RUN-DURABLE")["status"], "RESOLVED")
        self.assertEqual(reopened.get_trace("RUN-DURABLE")["step_count"], 2)


class TestOrchestratorPersistence(unittest.TestCase):
    """The orchestrator must persist every terminal outcome it produces."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.store = IncidentStore(db_path=f"{self._tmp.name}/runs.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_resolved_run_is_persisted_with_replayable_trace(self):
        orchestrator = build_isolated_orchestrator(store=self.store)
        incident = Incident(
            id="INC-PERSIST-OK",
            title="Payment OOM",
            description="payment-service OOMKilled, memory 96%",
            service="payment-service",
            environment="prod",
        )
        result = orchestrator.run_incident(incident=incident)
        run_id = result["run_id"]

        persisted = self.store.get_run(run_id)
        self.assertEqual(persisted["status"], "RESOLVED")
        self.assertEqual(persisted["service"], "payment-service")
        self.assertEqual(persisted["proposal"]["tool_name"], "simulate_restart")

        # The persisted trace must be replayable without the original process.
        trace = self.store.get_trace(run_id)
        self.assertIsNotNone(trace)
        summary = TraceReplayer.replay_summary(trace)
        self.assertIn(run_id, summary)
        self.assertIn("State Transitions", summary)

    def test_held_run_is_persisted(self):
        orchestrator = build_isolated_orchestrator(store=self.store)
        incident = Incident(
            id="INC-PERSIST-HOLD",
            title="Auth saturation",
            description="auth-service CPU 94%, redis timeouts",
            service="auth-service",
            environment="prod",
        )
        result = orchestrator.run_incident(incident=incident)

        persisted = self.store.get_run(result["run_id"])
        self.assertEqual(persisted["status"], "BLOCKED_FOR_APPROVAL")
        self.assertEqual(persisted["final_state"], "AWAITING_APPROVAL")
        self.assertIsNone(persisted["execution_result"])

    def test_rejected_run_is_persisted_with_reason(self):
        orchestrator = build_isolated_orchestrator(store=self.store)
        incident = Incident(
            id="INC-PERSIST-REJECT",
            title="Cross-env attempt",
            description="staging session targeting prod",
            service="payment-service",
            environment="prod",
        )
        result = orchestrator.run_incident(incident=incident, env_context="staging")

        persisted = self.store.get_run(result["run_id"])
        self.assertEqual(persisted["status"], "REJECTED")
        self.assertIn("CROSS_ENVIRONMENT_VIOLATION", persisted["reason"])


if __name__ == "__main__":
    unittest.main()


class TestDurableCheckpointing(unittest.TestCase):
    """
    A held Tier-3 run must be resumable by a different orchestrator instance.

    This is the property that makes human-in-the-loop approval usable in a real
    deployment: the engineer who approves an action is rarely in the same
    process that proposed it.
    """

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = f"{self._tmp.name}/runs.db"
        self.checkpoint_path = f"{self._tmp.name}/checkpoints.db"
        self._opened = []
        self.incident = Incident(
            id="INC-CHECKPOINT",
            title="Auth saturation requiring approval",
            description="auth-service CPU 94% with redis connection timeouts",
            service="auth-service",
            environment="prod",
        )

    def tearDown(self):
        # Close checkpointer handles before removing the directory; an open
        # SQLite handle blocks file deletion on Windows.
        for orchestrator in self._opened:
            orchestrator.close()
        self._tmp.cleanup()

    def _orchestrator(self):
        from app.tools.server import InfraToolServer
        from mini_platform.orchestrator.state_machine import IncidentOrchestrator
        from mini_platform.tools.mock_infrastructure import MockInfrastructureCluster

        orchestrator = IncidentOrchestrator(
            tool_server=InfraToolServer(MockInfrastructureCluster()),
            store=IncidentStore(db_path=self.db_path),
            checkpoint_db=self.checkpoint_path,
        )
        self._opened.append(orchestrator)
        return orchestrator

    def test_sqlite_backend_is_selected_for_a_path(self):
        from mini_platform.orchestrator.checkpointing import (
            build_checkpointer,
            sqlite_checkpointer_available,
        )

        if not sqlite_checkpointer_available():
            self.skipTest("langgraph-checkpoint-sqlite is not installed")
        from langgraph.checkpoint.memory import MemorySaver

        from mini_platform.orchestrator.checkpointing import close_checkpointer

        saver = build_checkpointer(self.checkpoint_path)
        try:
            self.assertNotIsInstance(saver, MemorySaver)
        finally:
            close_checkpointer(saver)

    def test_memory_backend_is_selected_without_a_path(self):
        from langgraph.checkpoint.memory import MemorySaver

        from mini_platform.orchestrator.checkpointing import build_checkpointer

        self.assertIsInstance(build_checkpointer(None), MemorySaver)

    def test_held_run_resumes_in_a_fresh_orchestrator(self):
        from mini_platform.orchestrator.checkpointing import sqlite_checkpointer_available

        if not sqlite_checkpointer_available():
            self.skipTest("langgraph-checkpoint-sqlite is not installed")

        run_id = "RUN-CROSS-PROCESS"
        first = self._orchestrator()
        held = first.run_incident(incident=self.incident, run_id=run_id)
        self.assertEqual(held["status"], "BLOCKED_FOR_APPROVAL")
        self.assertEqual(first.tool_gateway.mutating_call_count, 0)

        # A completely separate orchestrator, as a separate process would build.
        second = self._orchestrator()
        resumed = second.resume_incident(
            run_id=run_id, human_approval_token="TOKEN-HUMAN-APPROVED-SRE-CROSS"
        )
        self.assertEqual(resumed["status"], "RESOLVED")
        self.assertEqual(resumed["final_state"], "COMPLETED")
        self.assertEqual(second.tool_gateway.mutating_call_count, 1)

        # The persisted record reflects the final outcome, not the hold.
        self.assertEqual(IncidentStore(db_path=self.db_path).get_run(run_id)["status"], "RESOLVED")

    def test_resume_without_a_checkpoint_raises(self):
        from mini_platform.orchestrator.state_machine import OrchestrationException

        with self.assertRaises(OrchestrationException):
            self._orchestrator().resume_incident(
                run_id="RUN-NEVER-STARTED", human_approval_token="TOKEN-HUMAN-APPROVED-X"
            )
