"""
Tests for the post-action recovery healthcheck.

Regression context
------------------
The healthcheck previously requested a tool named ``query_logs``, which was
never registered. The call failed silently, ``post_logs`` was always empty, and
``has_fatal_logs`` was therefore always False -- so the fatal-log gate passed
vacuously on every run and no test noticed.

These tests pin the behaviour that catches that class of bug: the healthcheck
must read real logs, must reject a fake recovery, and must refuse to pass when
telemetry cannot be read at all.
"""
import unittest

from app.tools.contracts import TOOL_VERSIONS
from mini_platform.models import Incident
from mini_platform.orchestrator.langgraph_workflow import (
    RECOVERY_MAX_ERROR_RATE_PCT,
    RECOVERY_MAX_MEMORY_PCT,
    _has_fatal_logs,
)

from conftest import build_isolated_orchestrator, build_isolated_stack


class TestFatalLogDetection(unittest.TestCase):
    """Unit coverage for the fatal-log predicate."""

    def test_detects_fatal_and_critical_levels(self):
        self.assertTrue(_has_fatal_logs([{"level": "FATAL", "message": "heap gone"}]))
        self.assertTrue(_has_fatal_logs([{"level": "CRITICAL", "message": "down"}]))

    def test_detects_oomkilled_in_message_body(self):
        self.assertTrue(_has_fatal_logs([{"level": "WARN", "message": "pod OOMKilled"}]))

    def test_ignores_benign_levels(self):
        self.assertFalse(
            _has_fatal_logs(
                [
                    {"level": "INFO", "message": "recovered"},
                    {"level": "WARN", "message": "gc pause"},
                    {"level": "ERROR", "message": "single request failed"},
                ]
            )
        )

    def test_empty_log_set_reports_no_fatals(self):
        """An empty set is not evidence of health -- see the telemetry gate below."""
        self.assertFalse(_has_fatal_logs([]))


class TestHealthcheckReadsRealLogs(unittest.TestCase):
    """The healthcheck must use registered tool names and observe real data."""

    def test_only_registered_tool_names_are_used(self):
        """A healthcheck naming an unregistered tool would silently read nothing."""
        import inspect

        from mini_platform.orchestrator import langgraph_workflow

        source = inspect.getsource(langgraph_workflow.build_incident_graph)
        for quoted in ('tool_name="', "tool_name='"):
            for fragment in source.split(quoted)[1:]:
                name = fragment.split(quoted[-1])[0]
                self.assertIn(
                    name, TOOL_VERSIONS, f"workflow references unregistered tool '{name}'"
                )

    def test_recovery_observes_a_non_empty_log_set(self):
        orchestrator = build_isolated_orchestrator()
        result = orchestrator.run_incident(
            incident=Incident(
                id="INC-RECOVERY-LOGS",
                title="Payment OOM",
                description="payment-service OOMKilled, memory 96%",
                service="payment-service",
                environment="prod",
            )
        )
        log_health = result["recovery_verification"]["log_health"]
        self.assertGreater(
            log_health["post_log_count"],
            0,
            "the log health check read zero logs, so the fatal-log gate is vacuous",
        )
        self.assertFalse(log_health["has_fatal_logs"])
        self.assertTrue(result["recovery_verification"]["telemetry_available"])

    def test_remediation_clears_the_fatal_log_line(self):
        """The pre-remediation FATAL line must not survive a successful restart."""
        cluster, impl, gateway = build_isolated_stack()
        self.assertTrue(_has_fatal_logs(cluster.logs_db["payment-service"]))

        resp = gateway.execute_proposal(
            {
                "tool_name": "simulate_restart",
                "service": "payment-service",
                "environment": "prod",
                "parameters": {"reason": "flush heap"},
            },
            env_context="prod",
        )
        self.assertTrue(resp["ok"])
        self.assertFalse(_has_fatal_logs(cluster.logs_db["payment-service"]))


class TestHealthcheckRejectsFakeRecovery(unittest.TestCase):
    """A remediation that leaves the service unhealthy must not be reported healthy."""

    def _run_with_broken_remediation(self, mutate_after):
        """Run a workflow whose mutation reports success but leaves damage."""
        cluster, impl, gateway = build_isolated_stack()
        from mini_platform.orchestrator.state_machine import IncidentOrchestrator

        orchestrator = IncidentOrchestrator(tool_server=impl, tool_gateway=gateway)
        real_execute = gateway.execute_proposal

        def execute_then_damage(proposal, **kwargs):
            resp = real_execute(proposal, **kwargs)
            if resp.get("ok") and proposal.get("tool_name", "").startswith("simulate_"):
                mutate_after(cluster)
            return resp

        gateway.execute_proposal = execute_then_damage
        result = orchestrator.run_incident(
            incident=Incident(
                id="INC-FAKE-RECOVERY",
                title="Payment OOM",
                description="payment-service OOMKilled, memory 96%",
                service="payment-service",
                environment="prod",
            )
        )
        return result

    def test_persistent_fatal_log_fails_recovery(self):
        def leave_fatal_log(cluster):
            cluster.logs_db["payment-service"].append(
                {"timestamp": "2026-09-04T00:00:00Z", "level": "FATAL", "message": "still leaking"}
            )

        result = self._run_with_broken_remediation(leave_fatal_log)
        self.assertEqual(result["status"], "RECOVERY_VERIFICATION_FAILED")
        self.assertEqual(result["final_state"], "FAILED")
        self.assertTrue(result["recovery_verification"]["log_health"]["has_fatal_logs"])
        self.assertFalse(result["recovery_verification"]["recovered"])

    def test_memory_above_threshold_fails_recovery(self):
        def leave_high_memory(cluster):
            cluster.get_service("payment-service")["memory_utilization_pct"] = (
                RECOVERY_MAX_MEMORY_PCT + 10.0
            )

        result = self._run_with_broken_remediation(leave_high_memory)
        self.assertEqual(result["status"], "RECOVERY_VERIFICATION_FAILED")
        self.assertFalse(result["recovery_verification"]["recovered"])

    def test_error_rate_above_threshold_fails_recovery(self):
        def leave_high_errors(cluster):
            cluster.get_service("payment-service")["error_rate_pct"] = (
                RECOVERY_MAX_ERROR_RATE_PCT + 5.0
            )

        result = self._run_with_broken_remediation(leave_high_errors)
        self.assertEqual(result["status"], "RECOVERY_VERIFICATION_FAILED")
        self.assertFalse(result["recovery_verification"]["recovered"])

    def test_unhealthy_status_fails_recovery(self):
        def leave_degraded(cluster):
            cluster.get_service("payment-service")["status"] = "DEGRADED"

        result = self._run_with_broken_remediation(leave_degraded)
        self.assertEqual(result["status"], "RECOVERY_VERIFICATION_FAILED")
        self.assertFalse(
            result["recovery_verification"]["status_comparison"]["healthy"]
        )

    def test_unreadable_telemetry_fails_recovery(self):
        """No telemetry means nothing was verified; that must not pass."""
        def delete_service(cluster):
            del cluster.services["payment-service"]

        result = self._run_with_broken_remediation(delete_service)
        self.assertEqual(result["status"], "RECOVERY_VERIFICATION_FAILED")
        self.assertFalse(result["recovery_verification"]["telemetry_available"])
        self.assertIn("telemetry", result["reason"].lower())


class TestRecoveryDeltas(unittest.TestCase):
    """Before/after comparison must be reported for post-mortem review."""

    def test_deltas_are_recorded(self):
        orchestrator = build_isolated_orchestrator()
        result = orchestrator.run_incident(
            incident=Incident(
                id="INC-DELTAS",
                title="Payment OOM",
                description="payment-service OOMKilled, memory 96%",
                service="payment-service",
                environment="prod",
            )
        )
        deltas = result["recovery_verification"]["metric_deltas"]
        self.assertGreater(deltas["memory_utilization"]["reduction_pct"], 0)
        self.assertGreater(deltas["error_rate"]["reduction_pct"], 0)
        self.assertGreater(deltas["p99_latency_ms"]["reduction_ms"], 0)
        self.assertEqual(
            result["recovery_verification"]["thresholds"]["max_memory_pct"],
            RECOVERY_MAX_MEMORY_PCT,
        )


if __name__ == "__main__":
    unittest.main()
