"""
Artifact version manifest check.

Treating agents, prompts, and tools as versioned artifacts only means something
if the declared versions are enforced. This script compares ``VERSION.json``
against the versions the code actually reports and exits non-zero on any drift,
so a behaviour change cannot merge without an explicit version bump -- which is
what gives every deployment a precise rollback target.

Usage:
    python -m tools.check_versions
"""
from __future__ import annotations

import json
import pathlib
import sys
import tomllib
from typing import Dict, List, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "VERSION.json"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def _load_manifest() -> Dict:
    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _actual_versions() -> Dict[str, Dict[str, str]]:
    """
    Collect the versions the running code reports.

    Every section declared in VERSION.json is represented here, so the manifest
    cannot declare a component the gate does not actually verify.
    """
    from app.tools import client as tool_client
    from app.tools import gateway as tool_gateway
    from app.tools.contracts import TOOL_VERSIONS
    from mini_platform.agents.investigator import InvestigatorAgent
    from mini_platform.agents.ops import OpsAgent
    from mini_platform.agents.planner import PlannerAgent
    from mini_platform.agents.verifier import VerifierAgent
    from mini_platform.api.server import API_VERSION
    from mini_platform.knowledge import corpus, hybrid_rag, knowledge_graph
    from mini_platform.llm import __version__ as llm_version
    from mini_platform.llm import mock as llm_mock
    from mini_platform.llm import prompts as llm_prompts
    from mini_platform.llm import provider as llm_provider
    from mini_platform.safety import approval, guardrails
    from mini_platform.tracing import tracer

    return {
        "agents": {
            "planner": PlannerAgent().version,
            "investigator": InvestigatorAgent().version,
            "ops": OpsAgent().version,
            "verifier": VerifierAgent().version,
        },
        "tools": dict(TOOL_VERSIONS),
        "gateway": {
            "tool_gateway": tool_gateway.__version__,
            "tool_client": tool_client.__version__,
        },
        "knowledge": {
            "hybrid_rag": hybrid_rag.__version__,
            "knowledge_graph": knowledge_graph.__version__,
            "corpus": corpus.__version__,
        },
        "safety": {
            "guardrails": guardrails.__version__,
            # The approval token format is a signing contract: a change to it
            # invalidates every token already issued against the old scheme.
            "approval_tokens": approval.__version__,
            # Tier semantics live in the AutonomyTier enum; a change to the tier
            # set is a breaking change for every consumer of a safety decision.
            "autonomy_tiers": "1.0.0",
        },
        "llm": {
            # Prompts are versioned artifacts in their own right: a prompt change
            # is a behaviour change, and a trace records which one produced a
            # decision so a bad run is attributable.
            "llm_layer": llm_version,
            "provider_interface": llm_provider.__version__,
            "prompts": llm_prompts.__version__,
            "offline_providers": llm_mock.__version__,
        },
        "observability": {"tracer": tracer.__version__},
        "api": {"http_api": API_VERSION},
    }


def _compare(section: str, declared: Dict[str, str], actual: Dict[str, str]) -> List[str]:
    """Return a list of human-readable drift messages for one manifest section."""
    problems: List[str] = []

    for name, actual_version in sorted(actual.items()):
        if name not in declared:
            problems.append(
                f"{section}.{name}: present in code at {actual_version} but missing from VERSION.json"
            )
        elif declared[name] != actual_version:
            problems.append(
                f"{section}.{name}: VERSION.json declares {declared[name]} but code reports "
                f"{actual_version}"
            )

    for name in sorted(set(declared) - set(actual)):
        problems.append(f"{section}.{name}: declared in VERSION.json but not found in code")

    return problems


def check() -> Tuple[bool, List[str]]:
    """Run every consistency check. Returns (ok, problems)."""
    manifest = _load_manifest()
    components = manifest.get("components", {})
    actual = _actual_versions()
    problems: List[str] = []

    for section, actual_versions in actual.items():
        problems.extend(_compare(section, components.get(section, {}), actual_versions))

    # The platform version must agree with the packaging metadata.
    with PYPROJECT_PATH.open("rb") as handle:
        project_version = tomllib.load(handle)["project"]["version"]
    if manifest.get("platform_version") != project_version:
        problems.append(
            f"platform_version: VERSION.json declares {manifest.get('platform_version')} "
            f"but pyproject.toml declares {project_version}"
        )

    # A rollback story is only real if it is written down.
    rollback = manifest.get("rollback_strategy", {})
    for field in ("policy", "enforcement", "rollback_procedure", "compatibility_contract"):
        if not rollback.get(field):
            problems.append(f"rollback_strategy.{field}: missing or empty")

    return (not problems), problems


def main() -> int:
    ok, problems = check()

    if ok:
        manifest = _load_manifest()
        print(f"Version manifest OK (platform {manifest['platform_version']}).")
        for section, versions in sorted(manifest["components"].items()):
            rendered = ", ".join(f"{k}@{v}" for k, v in sorted(versions.items()))
            print(f"  {section}: {rendered}")
        return 0

    print("Version manifest drift detected:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "\nBump the component version in code and update VERSION.json, so the "
        "release has an explicit rollback target.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
