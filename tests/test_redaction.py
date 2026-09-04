"""
Tests for trace redaction.

Redaction runs before anything reaches disk, so a gap here leaks into every
persisted trace and into the audit database.

Regression context: the email pattern previously bounded the final domain label
to 7 characters, so `checkout-eng@corp.internal` -- the form every service owner
in this platform uses -- passed through unredacted into every trace.
"""
import json
import re
import unittest

from mini_platform.models import Incident
from mini_platform.tracing.tracer import redact_sensitive_data

from conftest import build_isolated_orchestrator

EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+-]*[A-Za-z][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class TestRedactionPatterns(unittest.TestCase):
    def test_internal_domain_emails_are_redacted(self):
        for address in (
            "checkout-eng@corp.internal",
            "security-infra@corp.internal",
            "a.b@mail.corp.internal",
            "ops+oncall@team.corporate",
        ):
            with self.subTest(address=address):
                self.assertEqual(redact_sensitive_data(address), "[REDACTED_EMAIL]")

    def test_public_domain_emails_are_redacted(self):
        self.assertEqual(redact_sensitive_data("jane.doe@enterprise.com"), "[REDACTED_EMAIL]")

    def test_tool_version_identifiers_survive_redaction(self):
        """Provenance depends on `tool@version`; it must not be mistaken for an email."""
        for identifier in (
            "simulate_restart@1.2.0",
            "INVOKE_simulate_restart@1.2.0",
            "get_metrics@1.0.0",
        ):
            with self.subTest(identifier=identifier):
                self.assertEqual(redact_sensitive_data(identifier), identifier)

    def test_secret_like_keys_are_replaced(self):
        scrubbed = redact_sensitive_data(
            {
                "service": "payment-service",
                "api_key": "sk-live-secret-12345",
                "db_password": "SuperSecret!",
                "nested": {"auth_header": "Bearer eyJhbGciOi"},
            }
        )
        self.assertEqual(scrubbed["api_key"], "[REDACTED]")
        self.assertEqual(scrubbed["db_password"], "[REDACTED]")
        self.assertEqual(scrubbed["nested"]["auth_header"], "[REDACTED]")
        self.assertEqual(scrubbed["service"], "payment-service")

    def test_card_numbers_are_redacted(self):
        for card in ("4532-1234-5678-9010", "4532 1234 5678 9010", "4532123456789010"):
            with self.subTest(card=card):
                self.assertEqual(redact_sensitive_data(card), "[REDACTED_CARD]")

    def test_redaction_recurses_through_collections(self):
        scrubbed = redact_sensitive_data(
            {"owners": [{"email": "a@corp.internal"}, {"email": "b@corp.internal"}]}
        )
        self.assertEqual([o["email"] for o in scrubbed["owners"]], ["[REDACTED_EMAIL]"] * 2)


class TestTraceContainsNoIdentities(unittest.TestCase):
    """An end-to-end run must not persist owner identities."""

    def test_no_email_reaches_the_persisted_trace(self):
        orchestrator = build_isolated_orchestrator()
        result = orchestrator.run_incident(
            incident=Incident(
                id="INC-REDACTION",
                title="Payment OOM",
                description="payment-service OOMKilled, memory 96%",
                service="payment-service",
                environment="prod",
            )
        )
        blob = json.dumps(result["trace"])
        self.assertIn("[REDACTED_EMAIL]", blob, "owner identity was expected in the evidence path")
        self.assertEqual(
            EMAIL_IN_TEXT.findall(blob), [], "an unredacted email reached the persisted trace"
        )

    def test_tool_version_provenance_survives_in_the_trace(self):
        orchestrator = build_isolated_orchestrator()
        result = orchestrator.run_incident(
            incident=Incident(
                id="INC-PROVENANCE-REDACT",
                title="Payment OOM",
                description="payment-service OOMKilled, memory 96%",
                service="payment-service",
                environment="prod",
            )
        )
        blob = json.dumps(result["trace"])
        self.assertIn("simulate_restart@1.2.0", blob)
