"""
Tests for artifact versioning and the rollback story.

Agents, tools, and safety policy are versioned artifacts. These tests assert
that the manifest is enforceable (drift is detected) and that provenance is
recorded in the trace, which is what makes a rollback target identifiable after
a bad run.
"""
import json
import pathlib
import unittest

from app.tools.contracts import TOOL_VERSIONS
from mini_platform.models import Incident
from tools.check_versions import check

from conftest import build_isolated_orchestrator

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestVersionManifest(unittest.TestCase):
    def setUp(self):
        with (REPO_ROOT / "VERSION.json").open(encoding="utf-8") as handle:
            self.manifest = json.load(handle)

    def test_manifest_matches_the_running_code(self):
        ok, problems = check()
        self.assertTrue(ok, f"version manifest drift: {problems}")

    def test_manifest_declares_a_rollback_story(self):
        rollback = self.manifest["rollback_strategy"]
        for field in ("policy", "enforcement", "rollback_procedure", "compatibility_contract"):
            self.assertTrue(rollback.get(field), f"rollback_strategy.{field} is empty")
        self.assertGreaterEqual(len(rollback["rollback_procedure"]), 3)

    def test_every_registered_tool_is_versioned_in_the_manifest(self):
        declared = self.manifest["components"]["tools"]
        self.assertEqual(set(declared), set(TOOL_VERSIONS))


class TestTraceProvenance(unittest.TestCase):
    """A trace must identify the exact artifact versions that produced it."""

    def test_trace_records_the_dispatched_tool_version(self):
        orchestrator = build_isolated_orchestrator()
        result = orchestrator.run_incident(
            incident=Incident(
                id="INC-PROVENANCE",
                title="Payment OOM",
                description="payment-service OOMKilled, memory 96%",
                service="payment-service",
                environment="prod",
            )
        )
        selected = result["selected_tool_version"]
        self.assertEqual(selected, f"simulate_restart@{TOOL_VERSIONS['simulate_restart']}")
        self.assertEqual(result["replay_metadata"]["tool_version"], selected)

        # The executing step names the versioned tool it dispatched.
        exec_steps = [s for s in result["trace"]["steps"] if s["agent"] == "tool_gateway"]
        self.assertEqual(len(exec_steps), 1)
        self.assertIn(selected, exec_steps[0]["action"])

    def test_agent_versions_are_recorded_in_a2a_envelopes(self):
        orchestrator = build_isolated_orchestrator()
        result = orchestrator.run_incident(
            incident=Incident(
                id="INC-AGENT-VERSIONS",
                title="Payment OOM",
                description="payment-service OOMKilled, memory 96%",
                service="payment-service",
                environment="prod",
            )
        )
        payloads = [m["payload"] for m in result["a2a_messages"]]
        recorded = {k: v for p in payloads for k, v in p.items() if k.endswith("_version")}
        for key in ("plan_version", "investigator_version", "ops_version", "verifier_version"):
            self.assertIn(key, recorded, f"{key} missing from A2A provenance")
