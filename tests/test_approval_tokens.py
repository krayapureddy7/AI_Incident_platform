"""
Tests for cryptographically bound human approval tokens.

The token is the only thing between a Tier-3 proposal and a production
mutation, so these tests are written from the attacker's side: each one is a
way someone might try to approve an action they were never given authority
over.
"""
import time
import unittest

from app.tools.contracts import HUMAN_APPROVAL_TOKEN_PREFIX
from mini_platform.models import ActionProposal, AutonomyTier
from mini_platform.safety.approval import (
    ApprovalTokenError,
    mint_approval_token,
    proposal_binding,
    verify_approval_token,
)
from mini_platform.safety.guardrails import SafetyGuardrails


def make_proposal(**overrides) -> ActionProposal:
    fields = dict(
        action_id="ACT-BASE",
        tool_name="simulate_restart",
        service="auth-service",
        environment="prod",
        parameters={"reason": "restart after saturation"},
        reasoning="test",
        evidence_citations=[],
        estimated_blast_radius=6,
        autonomy_tier=AutonomyTier.TIER_3_HUMAN_APPROVAL,
    )
    fields.update(overrides)
    return ActionProposal(**fields)


class TestTokenAuthenticity(unittest.TestCase):
    def test_minted_token_verifies_for_its_own_proposal(self):
        proposal = make_proposal()
        verdict = verify_approval_token(mint_approval_token(proposal, "sre-1"), proposal)
        self.assertTrue(verdict.valid, verdict.reason)
        self.assertEqual(verdict.approver, "sre-1")

    def test_token_keeps_the_published_prefix(self):
        """Operators and log greps rely on the prefix staying recognisable."""
        token = mint_approval_token(make_proposal(), "sre-1")
        self.assertTrue(token.startswith(HUMAN_APPROVAL_TOKEN_PREFIX))

    def test_prefix_alone_does_not_approve(self):
        """The prefix is a public constant, so it must carry no authority."""
        for forgery in (
            HUMAN_APPROVAL_TOKEN_PREFIX,
            HUMAN_APPROVAL_TOKEN_PREFIX + "x",
            HUMAN_APPROVAL_TOKEN_PREFIX + "SRE-ON-CALL-2026",
            HUMAN_APPROVAL_TOKEN_PREFIX + "payload.signature",
        ):
            with self.subTest(token=forgery):
                self.assertFalse(verify_approval_token(forgery, make_proposal()).valid)

    def test_tampering_with_the_payload_breaks_the_signature(self):
        proposal = make_proposal()
        token = mint_approval_token(proposal, "sre-1")
        body = token[len(HUMAN_APPROVAL_TOKEN_PREFIX):]
        payload, _, signature = body.partition(".")

        tampered = f"{HUMAN_APPROVAL_TOKEN_PREFIX}{payload[:-2]}AA.{signature}"
        self.assertFalse(verify_approval_token(tampered, proposal).valid)

    def test_signature_from_another_token_does_not_transfer(self):
        first = mint_approval_token(make_proposal(action_id="ACT-1"), "sre-1")
        second = mint_approval_token(make_proposal(action_id="ACT-2"), "sre-1")

        payload = first[len(HUMAN_APPROVAL_TOKEN_PREFIX):].partition(".")[0]
        signature = second[len(HUMAN_APPROVAL_TOKEN_PREFIX):].partition(".")[2]
        spliced = f"{HUMAN_APPROVAL_TOKEN_PREFIX}{payload}.{signature}"

        self.assertFalse(verify_approval_token(spliced, make_proposal(action_id="ACT-1")).valid)

    def test_missing_token_is_reported_not_raised(self):
        verdict = verify_approval_token(None, make_proposal())
        self.assertFalse(verdict.valid)
        self.assertIn("No human approval token", verdict.reason)


class TestTokenBinding(unittest.TestCase):
    """An approval authorizes one action, not a service or a standing right."""

    def setUp(self):
        self.approved = make_proposal()
        self.token = mint_approval_token(self.approved, "sre-1")

    def test_rebinding_any_field_invalidates_the_token(self):
        divergences = {
            "action_id": {"action_id": "ACT-OTHER"},
            "tool": {"tool_name": "simulate_scale"},
            "service": {"service": "payment-service"},
            "environment": {"environment": "staging"},
            "tenant": {"tenant": "tenant-b"},
            "parameters": {"parameters": {"reason": "something else"}},
        }
        for label, override in divergences.items():
            with self.subTest(field=label):
                verdict = verify_approval_token(self.token, make_proposal(**override))
                self.assertFalse(verdict.valid)
                self.assertIn("different action", verdict.reason)

    def test_parameter_edits_are_detected_regardless_of_key_order(self):
        """Binding is by value, so reordering keys must not look like tampering."""
        proposal = make_proposal(parameters={"a": 1, "b": 2})
        token = mint_approval_token(proposal, "sre-1")
        self.assertTrue(verify_approval_token(token, make_proposal(parameters={"b": 2, "a": 1})).valid)
        self.assertFalse(verify_approval_token(token, make_proposal(parameters={"a": 1, "b": 3})).valid)

    def test_proposal_without_an_action_id_cannot_be_signed(self):
        with self.assertRaises(ApprovalTokenError):
            mint_approval_token(make_proposal(action_id=""), "sre-1")

    def test_binding_reads_dicts_and_dataclasses_alike(self):
        """The gateway passes dicts; the verifier passes dataclasses."""
        proposal = make_proposal()
        as_dict = {
            "action_id": proposal.action_id,
            "tool_name": proposal.tool_name,
            "service": proposal.service,
            "environment": proposal.environment,
            "tenant": proposal.tenant,
            "parameters": proposal.parameters,
        }
        self.assertEqual(proposal_binding(proposal), proposal_binding(as_dict))


class TestTokenExpiry(unittest.TestCase):
    def test_expired_token_is_rejected(self):
        proposal = make_proposal()
        stale = mint_approval_token(proposal, "sre-1", ttl_seconds=60, now=time.time() - 3600)
        verdict = verify_approval_token(stale, proposal)
        self.assertFalse(verdict.valid)
        self.assertIn("expired", verdict.reason)

    def test_token_inside_its_window_is_accepted(self):
        proposal = make_proposal()
        fresh = mint_approval_token(proposal, "sre-1", ttl_seconds=600, now=time.time() - 60)
        self.assertTrue(verify_approval_token(fresh, proposal).valid)

    def test_ttl_must_be_positive(self):
        with self.assertRaises(ApprovalTokenError):
            mint_approval_token(make_proposal(), "sre-1", ttl_seconds=0)


class TestGuardrailIntegration(unittest.TestCase):
    """The safety engine must act on the verdict, not on the token's shape."""

    def setUp(self):
        self.guardrails = SafetyGuardrails()

    def test_signed_token_releases_a_tier_3_action(self):
        proposal = make_proposal()
        result = self.guardrails.evaluate_proposal(
            proposal, env_context="prod", human_approval_token=mint_approval_token(proposal, "sre-1")
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.tier, AutonomyTier.TIER_3_HUMAN_APPROVAL)

    def test_unsigned_token_leaves_the_action_held(self):
        proposal = make_proposal()
        result = self.guardrails.evaluate_proposal(
            proposal,
            env_context="prod",
            human_approval_token=HUMAN_APPROVAL_TOKEN_PREFIX + "not-really-signed",
        )
        self.assertFalse(result.approved)
        self.assertTrue(result.requires_human_token)

    def test_a_rejected_token_is_recorded_as_an_audit_event(self):
        """'Nobody approved' and 'someone tried' must be distinguishable later."""
        proposal = make_proposal()
        result = self.guardrails.evaluate_proposal(
            proposal,
            env_context="prod",
            human_approval_token=HUMAN_APPROVAL_TOKEN_PREFIX + "not-really-signed",
        )
        policies = {check["policy"]: check for check in result.checks_performed}
        self.assertIn("APPROVAL_TOKEN_AUTHENTICITY", policies)
        self.assertFalse(policies["APPROVAL_TOKEN_AUTHENTICITY"]["passed"])

    def test_no_token_records_no_authenticity_failure(self):
        result = self.guardrails.evaluate_proposal(make_proposal(), env_context="prod")
        policies = {check["policy"] for check in result.checks_performed}
        self.assertNotIn("APPROVAL_TOKEN_AUTHENTICITY", policies)

    def test_approver_identity_reaches_the_audit_trail(self):
        proposal = make_proposal()
        result = self.guardrails.evaluate_proposal(
            proposal,
            env_context="prod",
            human_approval_token=mint_approval_token(proposal, "on-call-sre-42"),
        )
        governance = next(c for c in result.checks_performed if c["policy"] == "TIER_GOVERNANCE")
        self.assertEqual(governance["approver"], "on-call-sre-42")


if __name__ == "__main__":
    unittest.main()
