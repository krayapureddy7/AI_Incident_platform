"""
Tests for the incident remediation HTTP API.

The API must add no policy of its own: a Tier-3 action halts for approval over
HTTP exactly as it does through the CLI, and an unsafe request is rejected
rather than executed. Each test builds an app over an isolated cluster and a
temporary database.
"""
import tempfile
import unittest

from fastapi.testclient import TestClient

from mini_platform.api.server import create_app
from mini_platform.persistence.store import IncidentStore
from mini_platform.safety.approval import mint_approval_token

from conftest import build_isolated_orchestrator


class ApiTestCase(unittest.TestCase):
    """Base fixture building an isolated app instance."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = IncidentStore(db_path=f"{self._tmp.name}/api.db")
        self.orchestrator = build_isolated_orchestrator(store=self.store)
        self.client = TestClient(create_app(orchestrator=self.orchestrator, store=self.store))

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _payment_incident(**overrides):
        payload = {
            "title": "Payment Service Heap Saturation",
            "description": "payment-service OOMKilled, memory at 96.2%, 503s on checkout",
            "service": "payment-service",
            "environment": "prod",
        }
        payload.update(overrides)
        return payload


class TestOperationalEndpoints(ApiTestCase):
    def test_health(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["persisted_runs"], 0)

    def test_tool_catalog_is_published(self):
        resp = self.client.get("/tools")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["tools"]), 5)
        self.assertIn("simulate_restart", body["tool_versions"])
        for tool in body["tools"]:
            self.assertIn("parameters", tool)
            self.assertNotEqual(tool["read_only"], tool["mutating"])

    def test_openapi_schema_is_served(self):
        self.assertEqual(self.client.get("/openapi.json").status_code, 200)


class TestIncidentLifecycle(ApiTestCase):
    def test_submit_incident_resolves(self):
        resp = self.client.post("/incidents", json=self._payment_incident())
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["status"], "RESOLVED")
        self.assertEqual(body["final_state"], "COMPLETED")
        self.assertEqual(body["proposal"]["tool_name"], "simulate_restart")
        self.assertTrue(body["execution_result"]["ok"])

    def test_tier3_incident_halts_for_approval(self):
        resp = self.client.post(
            "/incidents",
            json=self._payment_incident(
                title="Auth saturation",
                description="auth-service CPU 94% with redis timeouts",
                service="auth-service",
            ),
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["status"], "BLOCKED_FOR_APPROVAL")
        self.assertEqual(body["final_state"], "AWAITING_APPROVAL")
        self.assertIsNone(body.get("execution_result"))
        self.assertEqual(self.orchestrator.tool_gateway.mutating_call_count, 0)

    def test_approval_resumes_and_completes(self):
        submit = self.client.post(
            "/incidents",
            json=self._payment_incident(
                title="Auth saturation",
                description="auth-service CPU 94% with redis timeouts",
                service="auth-service",
            ),
        ).json()
        self.assertEqual(submit["status"], "BLOCKED_FOR_APPROVAL")

        resp = self.client.post(
            f"/incidents/{submit['run_id']}/approve",
            json={
                "human_approval_token": mint_approval_token(
                    submit["proposal"], approver="api-sre-1"
                )
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "RESOLVED")
        self.assertEqual(body["final_state"], "COMPLETED")
        self.assertEqual(self.orchestrator.tool_gateway.mutating_call_count, 1)

    def test_unsigned_approval_over_http_is_refused(self):
        """The public token prefix must not be enough to release a held run."""
        submit = self.client.post(
            "/incidents",
            json=self._payment_incident(
                title="Auth saturation",
                description="auth-service CPU 94% with redis timeouts",
                service="auth-service",
            ),
        ).json()
        self.assertEqual(submit["status"], "BLOCKED_FOR_APPROVAL")

        resp = self.client.post(
            f"/incidents/{submit['run_id']}/approve",
            json={"human_approval_token": "TOKEN-HUMAN-APPROVED-API-SRE-1"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotEqual(resp.json()["status"], "RESOLVED")
        self.assertEqual(self.orchestrator.tool_gateway.mutating_call_count, 0)

    def test_cross_environment_request_is_rejected(self):
        """A prod-scoped deployment refuses an incident aimed at staging."""
        resp = self.client.post(
            "/incidents", json=self._payment_incident(environment="staging")
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["status"], "REJECTED")
        self.assertIn(
            "CROSS_ENVIRONMENT_VIOLATION", body["safety_result"]["policy_violations"][0]
        )
        self.assertEqual(self.orchestrator.tool_gateway.mutating_call_count, 0)

    def test_cross_tenant_request_is_rejected(self):
        resp = self.client.post(
            "/incidents", json=self._payment_incident(tenant="tenant-b")
        )
        body = resp.json()
        self.assertEqual(body["status"], "REJECTED")
        self.assertIn("CROSS_TENANT_VIOLATION", body["safety_result"]["policy_violations"][0])

    def test_request_body_cannot_widen_session_authority(self):
        """
        The environment barrier compares the proposal's target against the
        deployment's authority. If a caller could supply both sides it would
        only ever compare a value to itself, so the barrier has to ignore any
        session fields the request tries to smuggle in.
        """
        resp = self.client.post(
            "/incidents",
            json=self._payment_incident(
                environment="staging", env_context="staging", tenant_context="tenant-b"
            ),
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["status"], "REJECTED")
        self.assertIn(
            "CROSS_ENVIRONMENT_VIOLATION", body["safety_result"]["policy_violations"][0]
        )
        self.assertEqual(self.orchestrator.tool_gateway.mutating_call_count, 0)

    def test_health_publishes_session_authority(self):
        body = self.client.get("/health").json()
        self.assertEqual(body["session_environment"], "prod")
        self.assertEqual(body["session_tenant"], "default")

    def test_approval_of_unknown_run_returns_404(self):
        resp = self.client.post(
            "/incidents/RUN-NOPE/approve",
            json={"human_approval_token": "TOKEN-HUMAN-APPROVED-X"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_request_validation_rejects_empty_service(self):
        resp = self.client.post("/incidents", json=self._payment_incident(service=""))
        self.assertEqual(resp.status_code, 422)

    def test_listing_and_fetching_runs(self):
        run_id = self.client.post("/incidents", json=self._payment_incident()).json()["run_id"]

        listing = self.client.get("/incidents").json()
        self.assertEqual(listing["count"], 1)

        detail = self.client.get(f"/incidents/{run_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["run_id"], run_id)

        self.assertEqual(self.client.get("/incidents/RUN-MISSING").status_code, 404)

    def test_checkpoint_state_endpoint(self):
        run_id = self.client.post(
            "/incidents",
            json=self._payment_incident(
                title="Auth saturation",
                description="auth-service CPU 94% redis timeouts",
                service="auth-service",
            ),
        ).json()["run_id"]

        resp = self.client.get(f"/incidents/{run_id}/state")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["current_state"], "AWAITING_APPROVAL")


class TestObservabilityEndpoints(ApiTestCase):
    def test_trace_and_replay(self):
        run_id = self.client.post("/incidents", json=self._payment_incident()).json()["run_id"]

        trace = self.client.get(f"/traces/{run_id}")
        self.assertEqual(trace.status_code, 200)
        self.assertGreater(trace.json()["step_count"], 0)

        replay = self.client.get(f"/traces/{run_id}/replay")
        self.assertEqual(replay.status_code, 200)
        self.assertIn("State Transitions", replay.json()["replay"])

    def test_missing_trace_returns_404(self):
        self.assertEqual(self.client.get("/traces/RUN-MISSING").status_code, 404)


class TestKnowledgeEndpoints(ApiTestCase):
    def test_hybrid_search_returns_cited_results(self):
        resp = self.client.post(
            "/knowledge/search",
            json={"query": "payment memory leak rolling restart", "service": "payment-service"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertGreater(body["count"], 0)
        top = body["results"][0]
        self.assertEqual(top["doc_id"], "DOC-RB-PAY-001")
        for field in ("rrf_score", "bm25_score", "dense_score", "citation_snippet"):
            self.assertIn(field, top)

    def test_blast_radius_endpoint(self):
        resp = self.client.get("/services/payment-service/blast-radius")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["service_tier"], "tier-1")
        self.assertIn("order-service", body["direct_dependents"])

    def test_blast_radius_unknown_service_returns_404(self):
        self.assertEqual(self.client.get("/services/ghost/blast-radius").status_code, 404)

    def test_service_topology_endpoint(self):
        body = self.client.get("/services").json()
        self.assertGreater(len(body["nodes"]), 0)
        self.assertGreater(len(body["edges"]), 0)


if __name__ == "__main__":
    unittest.main()
