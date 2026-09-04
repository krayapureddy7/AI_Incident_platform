"""
Tests for Safety Guardrails, Autonomy Tiers, and Redaction Invariants.
"""
import unittest
from mini_platform.models import ActionProposal, AutonomyTier
from mini_platform.safety.guardrails import SafetyGuardrails
from mini_platform.tracing.tracer import redact_sensitive_data


class TestSafetyGuardrails(unittest.TestCase):
    def setUp(self):
        self.guardrails = SafetyGuardrails()

    def test_tier_2_approved_restart(self):
        proposal = ActionProposal(
            action_id="ACT-1",
            tool_name="simulate_restart",
            service="payment-service",
            environment="prod",
            parameters={"service": "payment-service", "reason": "reboot"},
            reasoning="test",
            evidence_citations=[],
            estimated_blast_radius=2,
            autonomy_tier=AutonomyTier.TIER_2_VERIFIED_AUTO
        )
        res = self.guardrails.evaluate_proposal(proposal, env_context="prod")
        self.assertTrue(res.approved)
        self.assertEqual(res.tier, AutonomyTier.TIER_2_VERIFIED_AUTO)

    def test_tier_0_blocked_without_human_token(self):
        proposal = ActionProposal(
            action_id="ACT-2",
            tool_name="simulate_restart",
            service="auth-service",  # tier-0
            environment="prod",
            parameters={"service": "auth-service", "reason": "auth restart"},
            reasoning="test",
            evidence_citations=[],
            estimated_blast_radius=3,
            autonomy_tier=AutonomyTier.TIER_3_HUMAN_APPROVAL
        )
        res = self.guardrails.evaluate_proposal(proposal, env_context="prod")
        self.assertFalse(res.approved)
        self.assertTrue(res.requires_human_token)
        self.assertEqual(res.tier, AutonomyTier.TIER_3_HUMAN_APPROVAL)

    def test_tier_0_allowed_with_valid_human_token(self):
        proposal = ActionProposal(
            action_id="ACT-3",
            tool_name="simulate_restart",
            service="auth-service",
            environment="prod",
            parameters={"service": "auth-service", "reason": "auth restart with token"},
            reasoning="test",
            evidence_citations=[],
            estimated_blast_radius=3,
            autonomy_tier=AutonomyTier.TIER_3_HUMAN_APPROVAL
        )
        res = self.guardrails.evaluate_proposal(
            proposal,
            env_context="prod",
            human_approval_token="TOKEN-HUMAN-APPROVED-88219"
        )
        self.assertTrue(res.approved)

    def test_cross_environment_hard_rejection(self):
        proposal = ActionProposal(
            action_id="ACT-4",
            tool_name="simulate_restart",
            service="payment-service",
            environment="prod",
            parameters={"service": "payment-service", "reason": "cross-env attack"},
            reasoning="test",
            evidence_citations=[],
            estimated_blast_radius=1,
            autonomy_tier=AutonomyTier.TIER_2_VERIFIED_AUTO
        )
        res = self.guardrails.evaluate_proposal(proposal, env_context="staging")
        self.assertFalse(res.approved)
        self.assertFalse(res.requires_human_token)  # invariant violation, cannot be overridden
        self.assertTrue(any("CROSS_ENVIRONMENT_VIOLATION" in v for v in res.policy_violations))

    def test_redaction_scrubbing(self):
        raw_data = {
            "service": "payment-service",
            "api_key": "sk-live-secret-key-12345",
            "db_password": "SuperSecretPassword!",
            "customer_email": "jane.doe@enterprise.com",
            "credit_card": "4532-1234-5678-9010",
            "nested": {
                "auth_header": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            }
        }
        scrubbed = redact_sensitive_data(raw_data)
        self.assertEqual(scrubbed["api_key"], "[REDACTED]")
        self.assertEqual(scrubbed["db_password"], "[REDACTED]")
        self.assertNotIn("4532", str(scrubbed))
        self.assertNotIn("jane.doe@", str(scrubbed))


if __name__ == "__main__":
    unittest.main()
