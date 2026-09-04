"""
End-to-end demo of the multi-agent incident remediation lifecycle.

Every value printed below is read back from the actual run: the plan, the cited
evidence, the proposal and its rejected alternatives, the safety verdict, the
executed tool call, and the audit trace. Nothing is narrated from a literal, so
the demo output is a true report of what the platform did.

Stages shown:
  1. Planning        -- incident decomposition and A2A delegation
  2. Retrieval       -- Hybrid RAG citations and gateway-mediated telemetry
  3. Blast radius    -- knowledge-graph traversal
  4. Verification    -- deterministic guardrails and autonomy tiers
  5. Action          -- Tool Gateway dispatch and recovery healthcheck
  6. Trace review    -- audit trail and post-mortem replay
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from app.tools.server import InfraToolServer

from .knowledge.knowledge_graph import GLOBAL_KNOWLEDGE_GRAPH
from .models import Incident
from .orchestrator.state_machine import IncidentOrchestrator
from .tools.mock_infrastructure import MockInfrastructureCluster
from .tracing.tracer import TraceReplayer

# ANSI colours, disabled when output is redirected or NO_COLOR is set.
_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code: str) -> str:
    return code if _COLOR else ""


BOLD, DIM, RESET = _c("\033[1m"), _c("\033[2m"), _c("\033[0m")
CYAN, GREEN, YELLOW = _c("\033[36m"), _c("\033[32m"), _c("\033[33m")
RED, MAGENTA, BLUE = _c("\033[31m"), _c("\033[35m"), _c("\033[34m")


def banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"{BOLD}{CYAN}> {title}{RESET}")
    print("=" * 72)


def _a2a(messages: List[Dict[str, Any]], index: int) -> Optional[Dict[str, Any]]:
    """Fetch a recorded A2A envelope by position, if present."""
    return messages[index] if 0 <= index < len(messages) else None


def _print_a2a(envelope: Optional[Dict[str, Any]]) -> None:
    """Print a real A2A envelope header from the run."""
    if not envelope:
        return
    print(
        f"{MAGENTA}[A2A]{RESET} {envelope['sender']} -> {envelope['recipient']}: "
        f"{envelope['message_type']}  {DIM}(msg {envelope['message_id'][:8]}, "
        f"corr {envelope['correlation_id']}){RESET}"
    )


def run_demo(service: str = "payment-service") -> Dict[str, Any]:
    """Execute one incident end to end and report what actually happened."""
    banner("MINI AGENTIC AI PLATFORM - PRODUCTION INCIDENT REMEDIATION DEMO")
    print(f"{DIM}Tool protocol: FastMCP | Agents: Planner, Investigator, Ops, Verifier{RESET}")
    print(
        f"{DIM}Safety: deterministic guardrails, 3-tier autonomy, knowledge-graph "
        f"blast radius{RESET}"
    )

    incident = Incident(
        id="INC-2026-0903",
        title="Payment Service P99 Spike & Heap OOM Failures",
        description=(
            "Alert: payment-service p99 latency exceeded 4500ms (threshold: 300ms). "
            "Kubelet restarting pods with exit code 137. Error rate 19.4% with 503s "
            "on checkout flow."
        ),
        service=service,
        environment="prod",
        severity="SEV-1",
    )

    print(f"\n{BOLD}[INCIDENT REPORTED]{RESET}")
    print(f"  Incident ID : {incident.id}")
    print(f"  Severity    : {RED}{BOLD}{incident.severity}{RESET}")
    print(f"  Service     : {YELLOW}{incident.service}{RESET} (env: {incident.environment})")
    print(f"  Description : {incident.description}")

    # One orchestrator over one private cluster; the gateway is built for it.
    orchestrator = IncidentOrchestrator(
        tool_server=InfraToolServer(MockInfrastructureCluster())
    )
    result = orchestrator.run_incident(incident=incident)

    trace = result.get("trace") or {}
    messages = result.get("a2a_messages", [])
    steps = {step["agent"]: step for step in trace.get("steps", [])}

    # -- 1. Planning --------------------------------------------------------
    banner("1. PLANNING & DELEGATION (Planner Agent)")
    _print_a2a(_a2a(messages, 0))
    planner_step = steps.get("planner", {})
    plan_out = planner_step.get("outputs_redacted", {})
    print(f"  Symptoms extracted : {plan_out.get('symptoms', [])}")
    print(f"  Tasks delegated    : {plan_out.get('task_count', 0)}")
    for task in plan_out.get("delegated_tasks", []):
        print(f"    - [{task['task_id']}] {task['action']} -> {task['purpose']}")
    for decision in planner_step.get("decisions", []):
        print(f"  {GREEN}+{RESET} {decision}")
    for rejected in planner_step.get("rejected_alternatives", []):
        print(f"  {RED}-{RESET} {rejected}")

    # -- 2. Retrieval -------------------------------------------------------
    banner("2. RETRIEVAL & EVIDENCE GATHERING (Investigator / RAG Agent)")
    _print_a2a(_a2a(messages, 1))
    inv_step = steps.get("investigator", {})
    inv_out = inv_step.get("outputs_redacted", {})
    metrics = inv_out.get("metrics_summary", {})
    print(f"  Telemetry via Tool Gateway ({BLUE}get_logs, get_metrics, get_dependency_graph{RESET}):")
    print(
        f"    Memory={metrics.get('memory_utilization_pct')}% "
        f"CPU={metrics.get('cpu_utilization_pct')}% "
        f"ErrorRate={metrics.get('error_rate_pct')}% "
        f"P99={metrics.get('p99_latency_ms')}ms"
    )
    print(f"  Root cause identified : {YELLOW}{inv_out.get('root_cause')}{RESET}")
    print(f"  Top cited document    : {GREEN}{inv_out.get('top_citation')}{RESET}")
    if inv_out.get("tool_errors"):
        print(f"  {RED}Gateway-rejected reads:{RESET} {inv_out['tool_errors']}")

    evidence_msg = _a2a(messages, 2) or {}
    for citation in (evidence_msg.get("payload", {}).get("evidence", {}).get("citations") or []):
        print(f"\n    {BOLD}Citation {citation['doc_id']}{RESET} - {citation['title']}")
        print(
            f"      RRF={citation['rrf_score']} "
            f"[BM25={citation['bm25_score']}, dense={citation['dense_score']}]"
        )
        print(f"      \"{citation['citation_snippet'][:150]}...\"")

    # -- 3. Blast radius ----------------------------------------------------
    banner("3. KNOWLEDGE GRAPH TRAVERSAL & BLAST RADIUS")
    safety = result.get("safety_result") or {}
    blast = safety.get("blast_radius_analysis") or GLOBAL_KNOWLEDGE_GRAPH.calculate_blast_radius(
        incident.service
    )
    print(f"  Service tier          : {YELLOW}{blast.get('service_tier')}{RESET}")
    print(f"  Owner                 : {blast.get('owner')}")
    print(f"  Direct dependents     : {blast.get('direct_dependents')}")
    print(f"  Transitive dependents : {blast.get('transitive_dependents')}")
    print(f"  Total affected        : {blast.get('total_affected_services')}")
    print(
        f"  Risk rating           : {YELLOW}{blast.get('risk_rating')}{RESET} "
        f"(tier-0 impacted: {blast.get('tier_0_impacted')})"
    )

    # -- 4. Proposal & verification -----------------------------------------
    banner("4. REMEDIATION SYNTHESIS & SAFETY VERIFICATION (Ops & Verifier)")
    _print_a2a(_a2a(messages, 2))
    proposal = result.get("proposal") or {}
    print(f"  Proposed action : {BOLD}{proposal.get('tool_name')}({proposal.get('service')}){RESET}")
    print(f"  Parameters      : {proposal.get('parameters')}")
    print(f"  Reasoning       : {proposal.get('reasoning')}")
    print("  Rejected alternatives:")
    for alt in proposal.get("rejected_alternatives", []):
        print(f"    {RED}-{RESET} {alt['alternative']}")
        print(f"      {DIM}{alt['reason_rejected']}{RESET}")

    print()
    _print_a2a(_a2a(messages, 3))
    print("  Deterministic policy checks performed:")
    for check in safety.get("checks_performed", []):
        mark = f"{GREEN}PASS{RESET}" if check.get("passed") else f"{RED}FAIL{RESET}"
        detail = check.get("details") or check.get("reason") or check.get("tier") or ""
        print(f"    [{mark}] {check['policy']} {DIM}{detail}{RESET}")
    verdict_colour = GREEN if safety.get("approved") else RED
    print(f"  Autonomy tier   : {safety.get('tier')}")
    print(f"  {verdict_colour}{BOLD}Verdict: {safety.get('explanation')}{RESET}")

    # -- 5. Execution & recovery -------------------------------------------
    banner("5. TOOL GATEWAY EXECUTION & RECOVERY HEALTHCHECK")
    exec_res = result.get("execution_result") or {}
    if not exec_res:
        print(f"  {YELLOW}No action executed.{RESET} Workflow halted at "
              f"{BOLD}{result.get('final_state')}{RESET}: {result.get('reason')}")
    else:
        exec_data = exec_res.get("data") or {}
        print(f"  Dispatched tool  : {BOLD}{result.get('selected_tool_version')}{RESET}")
        print(f"  Envelope         : ok={GREEN}{exec_res.get('ok')}{RESET}")
        print(f"  Action status    : {exec_data.get('status')}")
        print(f"  Message          : {exec_data.get('message')}")
        print(f"  Gateway dispatches: total={orchestrator.tool_gateway.fastmcp_call_count}, "
              f"mutating={orchestrator.tool_gateway.mutating_call_count}")

        recovery = result.get("recovery_verification") or {}
        deltas = recovery.get("metric_deltas", {})
        print("\n  Post-action verification (before -> after):")
        for label, key in (
            ("Memory saturation", "memory_utilization"),
            ("Error rate", "error_rate"),
            ("P99 latency", "p99_latency_ms"),
        ):
            delta = deltas.get(key, {})
            print(f"    {label:<18}: {delta.get('before')} -> {delta.get('after')}")
        status_cmp = recovery.get("status_comparison", {})
        print(f"    {'Status':<18}: {status_cmp.get('before')} -> "
              f"{GREEN}{BOLD}{status_cmp.get('after')}{RESET}")
        log_health = recovery.get("log_health", {})
        print(f"    {'Log health':<18}: {log_health.get('post_log_count')} lines read, "
              f"fatal={log_health.get('has_fatal_logs')}")
        print(f"    {'Telemetry read':<18}: {recovery.get('telemetry_available')}")
        recovered_colour = GREEN if recovery.get("recovered") else RED
        print(f"    {'Recovered':<18}: {recovered_colour}{BOLD}{recovery.get('recovered')}{RESET}")

    # -- 6. Trace review ----------------------------------------------------
    banner("6. AUDIT TRACE & POST-MORTEM REPLAY")
    print(f"  Run ID             : {BOLD}{result.get('run_id')}{RESET}")
    print(f"  Final state        : {GREEN}{BOLD}{result.get('final_state')}{RESET}")
    print(f"  Trace file         : {result.get('trace_file')}")
    print(f"  Steps recorded     : {trace.get('step_count')}")
    print(f"  A2A messages       : {trace.get('message_count')}")
    print(f"  State transitions  : {len(trace.get('state_transitions', []))}")
    print(f"  Total latency      : {trace.get('total_latency_ms')}ms")
    print(
        f"  Estimated tokens   : {trace.get('total_tokens')} "
        f"(${trace.get('total_cost_usd', 0.0):.5f} USD)"
    )

    print(f"\n{BOLD}Replay (reconstructed from the persisted trace):{RESET}")
    print(TraceReplayer.replay_summary(trace))

    banner(f"DEMO COMPLETE - workflow terminated in state {result.get('final_state')}")
    return result


def main() -> int:
    """Entry point. Non-zero exit if the workflow did not resolve."""
    result = run_demo()
    return 0 if result.get("status") == "RESOLVED" else 1


if __name__ == "__main__":
    sys.exit(main())
