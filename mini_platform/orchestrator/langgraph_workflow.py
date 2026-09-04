"""
LangGraph Incident Remediation Workflow.

Defines the StateGraph runtime, typed state schema, nodes, and conditional edges
governing multi-agent incident triage, planning, investigation, proposing,
safety audit, Tool Gateway execution, and recovery healthcheck.

Control-flow determinism
------------------------
Routing decisions read only structured, validated fields produced by the
deterministic safety engine (``safety_result``) or by tool envelopes
(``execution_result.ok``). No free-form agent text influences a transition.

Every node is registered through ``with_resilience`` so timeouts are enforced
and transient failures are retried. The executor node -- the only node that
mutates infrastructure -- is given a timeout but is never auto-retried.
"""
import time
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.tools.contracts import tool_version

from ..models import A2AMessage, AgentRole, MessageType, WorkflowState
from ..tracing.tracer import AuditTracer
from .resilience import with_resilience

#: Nodes that only read or compute may be retried on transient failure.
RETRYABLE_NODES = frozenset(
    {"triage", "planner", "investigator", "ops", "verifier", "verify_recovery"}
)

#: Recovery thresholds applied by the post-action healthcheck.
RECOVERY_MAX_MEMORY_PCT = 75.0
RECOVERY_MAX_ERROR_RATE_PCT = 1.0
FATAL_LOG_LEVELS = frozenset({"CRITICAL", "FATAL"})


class IncidentGraphState(TypedDict):
    """
    Typed state dictionary passed through LangGraph nodes.
    Represents the full incident lifecycle context.
    """

    run_id: str
    incident: Dict[str, Any]
    env_context: str
    tenant_context: Optional[str]
    human_approval_token: Optional[str]
    tracer: Optional[Any]
    current_state: str
    workflow_status: str
    status_reason: Optional[str]

    # Structured A2A messaging envelopes
    last_a2a_message: Optional[Dict[str, Any]]
    a2a_messages: List[Dict[str, Any]]

    # Agent structured outputs
    plan: Optional[Dict[str, Any]]
    evidence: Optional[Dict[str, Any]]
    proposal: Optional[Dict[str, Any]]
    safety_result: Optional[Dict[str, Any]]

    # Baseline and post-action telemetry
    before_metrics: Optional[Dict[str, Any]]
    before_logs: Optional[List[Dict[str, Any]]]

    # Execution and recovery
    selected_tool_version: Optional[str]
    execution_result: Optional[Dict[str, Any]]
    post_check: Optional[Dict[str, Any]]
    recovery_verification: Optional[Dict[str, Any]]
    trace_file: Optional[str]


def _get_tracer(state: IncidentGraphState) -> Optional[AuditTracer]:
    """Resolve the tracer for a run, preferring the instance carried in state."""
    tracer = state.get("tracer")
    if isinstance(tracer, AuditTracer):
        return tracer
    run_id = state.get("run_id")
    if run_id:
        return AuditTracer.get(run_id)
    return None


def _has_fatal_logs(logs: List[Dict[str, Any]]) -> bool:
    """True when any log entry indicates an unrecovered fatal condition."""
    return any(
        entry.get("level") in FATAL_LOG_LEVELS or "OOMKilled" in entry.get("message", "")
        for entry in logs
    )


def build_incident_graph(
    orchestrator: Any,
    checkpointer: Optional[Any] = None,
    max_retries: int = 2,
    step_timeout_sec: float = 15.0,
) -> Any:
    """
    Construct and compile the LangGraph StateGraph workflow for incident remediation.

    All agent nodes (Planner, Investigator, Ops, Verifier) communicate via typed
    Pydantic ``A2AMessage`` envelopes and are registered as LangGraph nodes with
    retry/timeout guards. Supports persistent checkpointing via a checkpointer.
    """
    builder = StateGraph(IncidentGraphState)

    # 1. Triage Node (IDLE -> TRIAGE -> PLANNING via A2A envelope)
    def triage_node(state: IncidentGraphState) -> Dict[str, Any]:
        tracer = _get_tracer(state)
        incident = state.get("incident", {})
        run_id = state.get("run_id", "UNKNOWN")
        if tracer:
            tracer.record_transition(
                "IDLE", "TRIAGE", "TRIGGER_INCIDENT_INGESTION", {"incident_id": incident.get("id")}
            )
            tracer.record_transition("TRIAGE", "PLANNING", "DISPATCH_TO_PLANNER")

        triage_msg = A2AMessage(
            sender=AgentRole.ORCHESTRATOR,
            recipient=AgentRole.PLANNER,
            message_type=MessageType.TASK_DELEGATION,
            correlation_id=run_id,
            payload={"incident": incident},
        )
        if tracer:
            tracer.record_message(triage_msg)

        msg_envelope = triage_msg.to_dict()
        return {
            "current_state": WorkflowState.PLANNING.value,
            "last_a2a_message": msg_envelope,
            "a2a_messages": [msg_envelope],
        }

    # 2. Planner Node
    def planner_node(state: IncidentGraphState) -> Dict[str, Any]:
        tracer = _get_tracer(state)
        run_id = state.get("run_id", "UNKNOWN")

        inbound_dict = state.get("last_a2a_message")
        if inbound_dict:
            inbound_msg = A2AMessage.model_validate(inbound_dict)
        else:
            inbound_msg = A2AMessage(
                sender=AgentRole.ORCHESTRATOR,
                recipient=AgentRole.PLANNER,
                message_type=MessageType.TASK_DELEGATION,
                correlation_id=run_id,
                payload={"incident": state.get("incident", {})},
            )

        outbound_msg = orchestrator.planner.handle_message(inbound_msg, {"tracer": tracer})
        if tracer:
            tracer.record_message(outbound_msg)

        msg_list = list(state.get("a2a_messages") or [])
        msg_list.append(outbound_msg.to_dict())

        return {
            "plan": outbound_msg.payload,
            "last_a2a_message": outbound_msg.to_dict(),
            "a2a_messages": msg_list,
            "current_state": WorkflowState.PLANNING.value,
        }

    # 3. Investigator Node
    def investigator_node(state: IncidentGraphState) -> Dict[str, Any]:
        tracer = _get_tracer(state)
        run_id = state.get("run_id", "UNKNOWN")
        if tracer:
            tracer.record_transition(
                state.get("current_state", "PLANNING"),
                WorkflowState.INVESTIGATING.value,
                "DELEGATE_INVESTIGATION_AND_RAG",
            )

        inbound_dict = state.get("last_a2a_message")
        if inbound_dict:
            inbound_msg = A2AMessage.model_validate(inbound_dict)
        else:
            inbound_msg = A2AMessage(
                sender=AgentRole.PLANNER,
                recipient=AgentRole.INVESTIGATOR,
                message_type=MessageType.TASK_DELEGATION,
                correlation_id=run_id,
                payload=state.get("plan", {}),
            )

        outbound_msg = orchestrator.investigator.handle_message(
            inbound_msg,
            {
                "env_context": state.get("env_context", "prod"),
                "tenant_context": state.get("tenant_context", "default"),
                "tracer": tracer,
            },
        )
        evidence = outbound_msg.payload.get("evidence", {})
        if tracer:
            tracer.record_message(outbound_msg)

        msg_list = list(state.get("a2a_messages") or [])
        msg_list.append(outbound_msg.to_dict())

        return {
            "evidence": evidence,
            "before_metrics": evidence.get("metrics", {}),
            "before_logs": evidence.get("logs", []),
            "last_a2a_message": outbound_msg.to_dict(),
            "a2a_messages": msg_list,
            "current_state": WorkflowState.INVESTIGATING.value,
        }

    # 4. Ops Node
    def ops_node(state: IncidentGraphState) -> Dict[str, Any]:
        tracer = _get_tracer(state)
        run_id = state.get("run_id", "UNKNOWN")
        if tracer:
            tracer.record_transition(
                state.get("current_state", "INVESTIGATING"),
                WorkflowState.PROPOSING_ACTION.value,
                "SYNTHESIZE_ACTION_PROPOSAL",
            )

        inbound_dict = state.get("last_a2a_message")
        if inbound_dict:
            inbound_msg = A2AMessage.model_validate(inbound_dict)
        else:
            inbound_msg = A2AMessage(
                sender=AgentRole.INVESTIGATOR,
                recipient=AgentRole.OPS,
                message_type=MessageType.EVIDENCE_REPORT,
                correlation_id=run_id,
                payload={
                    "incident": state.get("incident", {}),
                    "evidence": state.get("evidence", {}),
                },
            )

        outbound_msg = orchestrator.ops.handle_message(
            inbound_msg,
            {
                "env_context": state.get("env_context", "prod"),
                "tenant_context": state.get("tenant_context", "default"),
                "tracer": tracer,
            },
        )
        if tracer:
            tracer.record_message(outbound_msg)

        msg_list = list(state.get("a2a_messages") or [])
        msg_list.append(outbound_msg.to_dict())

        return {
            "proposal": outbound_msg.payload.get("proposal", {}),
            "last_a2a_message": outbound_msg.to_dict(),
            "a2a_messages": msg_list,
            "current_state": WorkflowState.PROPOSING_ACTION.value,
        }

    # 5. Verifier Node
    def verifier_node(state: IncidentGraphState) -> Dict[str, Any]:
        tracer = _get_tracer(state)
        run_id = state.get("run_id", "UNKNOWN")
        if tracer:
            tracer.record_transition(
                state.get("current_state", "PROPOSING_ACTION"),
                WorkflowState.SAFETY_VERIFICATION.value,
                "DISPATCH_TO_VERIFIER",
            )

        inbound_dict = state.get("last_a2a_message")
        if inbound_dict:
            inbound_msg = A2AMessage.model_validate(inbound_dict)
        else:
            inbound_msg = A2AMessage(
                sender=AgentRole.OPS,
                recipient=AgentRole.VERIFIER,
                message_type=MessageType.ACTION_PROPOSAL,
                correlation_id=run_id,
                payload={
                    "incident": state.get("incident", {}),
                    "proposal": state.get("proposal", {}),
                },
            )

        outbound_msg = orchestrator.verifier.handle_message(
            inbound_msg,
            {
                "human_approval_token": state.get("human_approval_token"),
                "env_context": state.get("env_context", "prod"),
                "tenant_context": state.get("tenant_context", "default"),
                "tracer": tracer,
            },
        )
        if tracer:
            tracer.record_message(outbound_msg)

        msg_list = list(state.get("a2a_messages") or [])
        msg_list.append(outbound_msg.to_dict())

        return {
            "safety_result": outbound_msg.payload.get("safety_result", {}),
            "last_a2a_message": outbound_msg.to_dict(),
            "a2a_messages": msg_list,
            "current_state": WorkflowState.SAFETY_VERIFICATION.value,
        }

    # 6. Safety Router (deterministic: reads only the safety engine's verdict)
    def safety_router(state: IncidentGraphState) -> str:
        safety_result = state.get("safety_result") or {}
        if safety_result.get("approved", False):
            return "executor"
        if safety_result.get("requires_human_token", False):
            return "hold_for_approval"
        return "policy_rejected"

    # 7. Hold For Human Approval Node
    def hold_for_approval_node(state: IncidentGraphState) -> Dict[str, Any]:
        tracer = _get_tracer(state)
        safety_result = state.get("safety_result", {})
        trace_path = None
        if tracer:
            tracer.record_transition(
                state.get("current_state", "SAFETY_VERIFICATION"),
                WorkflowState.AWAITING_APPROVAL.value,
                "HOLD_FOR_TIER_3_HUMAN_APPROVAL",
                safety_result,
            )
            trace_path = tracer.persist()

        return {
            "current_state": WorkflowState.AWAITING_APPROVAL.value,
            "workflow_status": "BLOCKED_FOR_APPROVAL",
            "status_reason": safety_result.get("explanation"),
            "trace_file": trace_path,
        }

    # 8. Policy Rejected Node
    def policy_rejected_node(state: IncidentGraphState) -> Dict[str, Any]:
        tracer = _get_tracer(state)
        safety_result = state.get("safety_result", {})
        trace_path = None
        if tracer:
            tracer.record_transition(
                state.get("current_state", "SAFETY_VERIFICATION"),
                WorkflowState.FAILED.value,
                "POLICY_VIOLATION_REJECTED",
                safety_result,
            )
            trace_path = tracer.persist()

        return {
            "current_state": WorkflowState.FAILED.value,
            "workflow_status": "REJECTED",
            "status_reason": safety_result.get("explanation"),
            "trace_file": trace_path,
        }

    # 9. Tool Gateway Executor Node (the only mutating node)
    def executor_node(state: IncidentGraphState) -> Dict[str, Any]:
        tracer = _get_tracer(state)
        if tracer:
            tracer.record_transition(
                state.get("current_state", "SAFETY_VERIFICATION"),
                WorkflowState.EXECUTING.value,
                "EXECUTE_VERIFIED_ACTION",
            )

        prop = state.get("proposal", {})
        tool_name = prop.get("tool_name", "")
        selected_tool_version = f"{tool_name}@{tool_version(tool_name)}"

        env_context = state.get("env_context", "prod")
        tenant_context = state.get("tenant_context", "default")
        incident = state.get("incident", {})

        gateway_proposal = {
            "action_id": prop.get("action_id"),
            "tool_name": tool_name,
            "service": prop.get("service", incident.get("service")),
            "environment": prop.get("environment", env_context),
            "tenant": prop.get("tenant", incident.get("tenant", tenant_context)),
            "parameters": prop.get("parameters", {}),
            "reasoning": prop.get("reasoning", ""),
            "estimated_blast_radius": prop.get("estimated_blast_radius", 0),
        }

        exec_start = time.time()
        tool_call_resp = orchestrator.tool_gateway.execute_proposal(
            proposal=gateway_proposal,
            env_context=env_context,
            tenant_context=tenant_context,
            caller_role=AgentRole.ORCHESTRATOR.value,
            human_approval_token=state.get("human_approval_token"),
        )
        exec_latency = (time.time() - exec_start) * 1000.0

        if tracer:
            tracer.record_step(
                step_id="STEP-EXECUTE-01",
                workflow_state="EXECUTING",
                agent="tool_gateway",
                action=f"INVOKE_{selected_tool_version}",
                inputs={
                    "tool": selected_tool_version,
                    "parameters": prop.get("parameters"),
                    "env": env_context,
                    "tenant": tenant_context,
                },
                outputs=tool_call_resp,
                latency_ms=exec_latency,
                decisions=[
                    f"Executed {selected_tool_version} through the Tool Gateway following "
                    "Verifier sign-off."
                ],
                rejected_alternatives=[],
            )

        return {
            "selected_tool_version": selected_tool_version,
            "execution_result": tool_call_resp,
            "current_state": WorkflowState.EXECUTING.value,
        }

    # 10. Execution Router
    def execution_router(state: IncidentGraphState) -> str:
        exec_res = state.get("execution_result", {}) or {}
        return "verify_recovery" if exec_res.get("ok") else "execution_failed"

    # 11. Execution Failed Node
    def execution_failed_node(state: IncidentGraphState) -> Dict[str, Any]:
        tracer = _get_tracer(state)
        tool_call_resp = state.get("execution_result", {}) or {}
        trace_path = None
        if tracer:
            tracer.record_transition(
                state.get("current_state", "EXECUTING"),
                WorkflowState.FAILED.value,
                "TOOL_EXECUTION_FAILURE",
                tool_call_resp,
            )
            trace_path = tracer.persist()

        return {
            "current_state": WorkflowState.FAILED.value,
            "workflow_status": "EXECUTION_FAILED",
            "status_reason": tool_call_resp.get("error", {}).get("message"),
            "trace_file": trace_path,
        }

    # 12. Verify Recovery Node (before/after telemetry and log comparison)
    def verify_recovery_node(state: IncidentGraphState) -> Dict[str, Any]:
        tracer = _get_tracer(state)
        if tracer:
            tracer.record_transition(
                state.get("current_state", "EXECUTING"),
                WorkflowState.VERIFYING_RECOVERY.value,
                "POST_ACTION_HEALTHCHECK",
            )

        prop = state.get("proposal", {})
        env_context = state.get("env_context", "prod")
        tenant_context = state.get("tenant_context", "default")
        service = prop.get("service")

        before_metrics = state.get("before_metrics") or {}

        # Post-action telemetry, read through the Tool Gateway like every other
        # tool call. Both reads use registered tool names -- an unregistered
        # name would surface as TOOL_NOT_FOUND rather than silently returning
        # empty data and vacuously passing the healthcheck.
        post_metrics_resp = orchestrator.tool_gateway.execute_read_only(
            tool_name="get_metrics",
            service=service,
            environment=env_context,
            tenant=tenant_context,
            caller_role=AgentRole.ORCHESTRATOR.value,
        )
        post_check = post_metrics_resp.get("data") or {}

        post_logs_resp = orchestrator.tool_gateway.execute_read_only(
            tool_name="get_logs",
            service=service,
            environment=env_context,
            tenant=tenant_context,
            caller_role=AgentRole.ORCHESTRATOR.value,
            extra_params={"timeframe": "2m", "limit": 20},
        )
        post_logs = (post_logs_resp.get("data") or {}).get("logs", [])

        # A healthcheck that cannot read telemetry has not verified anything.
        telemetry_available = bool(post_metrics_resp.get("ok")) and bool(post_logs_resp.get("ok"))

        before_status = before_metrics.get("status", "UNKNOWN")
        after_status = post_check.get("status", "UNKNOWN")

        before_mem = before_metrics.get("memory_utilization_pct", 0.0) or 0.0
        after_mem = post_check.get("memory_utilization_pct", 0.0) or 0.0
        before_err = before_metrics.get("error_rate_pct", 0.0) or 0.0
        after_err = post_check.get("error_rate_pct", 0.0) or 0.0
        before_p99 = before_metrics.get("p99_latency_ms", 0) or 0
        after_p99 = post_check.get("p99_latency_ms", 0) or 0

        is_healthy = after_status == "HEALTHY"
        is_mem_ok = after_mem <= RECOVERY_MAX_MEMORY_PCT
        is_err_ok = after_err <= RECOVERY_MAX_ERROR_RATE_PCT
        has_fatal_logs = _has_fatal_logs(post_logs)

        recovery_passed = (
            telemetry_available and is_healthy and is_mem_ok and is_err_ok and not has_fatal_logs
        )

        recovery_comparison = {
            "recovered": recovery_passed,
            "telemetry_available": telemetry_available,
            "status_comparison": {
                "before": before_status,
                "after": after_status,
                "healthy": is_healthy,
            },
            "metric_deltas": {
                "memory_utilization": {
                    "before": before_mem,
                    "after": after_mem,
                    "reduction_pct": round(before_mem - after_mem, 2),
                },
                "error_rate": {
                    "before": before_err,
                    "after": after_err,
                    "reduction_pct": round(before_err - after_err, 2),
                },
                "p99_latency_ms": {
                    "before": before_p99,
                    "after": after_p99,
                    "reduction_ms": before_p99 - after_p99,
                },
            },
            "log_health": {
                "post_log_count": len(post_logs),
                "has_fatal_logs": has_fatal_logs,
            },
            "thresholds": {
                "max_memory_pct": RECOVERY_MAX_MEMORY_PCT,
                "max_error_rate_pct": RECOVERY_MAX_ERROR_RATE_PCT,
            },
        }

        if recovery_passed:
            trace_path = None
            if tracer:
                tracer.record_transition(
                    WorkflowState.VERIFYING_RECOVERY.value,
                    WorkflowState.COMPLETED.value,
                    "INCIDENT_REMEDIATION_CONFIRMED",
                    recovery_comparison,
                )
                trace_path = tracer.persist()

            return {
                "post_check": post_check,
                "recovery_verification": recovery_comparison,
                "current_state": WorkflowState.COMPLETED.value,
                "workflow_status": "RESOLVED",
                "trace_file": trace_path,
            }

        if not telemetry_available:
            reason = "Post-action verification failed: telemetry could not be read."
        else:
            reason = (
                f"Post-action verification failed: status={after_status}, mem={after_mem}%, "
                f"err={after_err}%, fatal_logs={has_fatal_logs}"
            )

        trace_path = None
        if tracer:
            tracer.record_transition(
                WorkflowState.VERIFYING_RECOVERY.value,
                WorkflowState.FAILED.value,
                "RECOVERY_VERIFICATION_FAILED",
                recovery_comparison,
            )
            trace_path = tracer.persist()

        return {
            "post_check": post_check,
            "recovery_verification": recovery_comparison,
            "current_state": WorkflowState.FAILED.value,
            "workflow_status": "RECOVERY_VERIFICATION_FAILED",
            "status_reason": reason,
            "trace_file": trace_path,
        }

    # 13. Recovery Router
    def recovery_router(state: IncidentGraphState) -> str:
        return (
            "remediation_complete"
            if state.get("workflow_status") == "RESOLVED"
            else "recovery_failed"
        )

    # 14. Recovery Failed Node
    def recovery_failed_node(state: IncidentGraphState) -> Dict[str, Any]:
        tracer = _get_tracer(state)
        trace_path = None
        if tracer:
            tracer.record_transition(
                state.get("current_state", "VERIFYING_RECOVERY"),
                WorkflowState.FAILED.value,
                "RECOVERY_VERIFICATION_REJECTED",
                {"reason": state.get("status_reason")},
            )
            trace_path = tracer.persist()

        return {
            "current_state": WorkflowState.FAILED.value,
            "workflow_status": "RECOVERY_VERIFICATION_FAILED",
            "trace_file": trace_path,
        }

    # -- Node registration with resilience guards ---------------------------
    node_bodies = {
        "triage": triage_node,
        "planner": planner_node,
        "investigator": investigator_node,
        "ops": ops_node,
        "verifier": verifier_node,
        "hold_for_approval": hold_for_approval_node,
        "policy_rejected": policy_rejected_node,
        "executor": executor_node,
        "execution_failed": execution_failed_node,
        "verify_recovery": verify_recovery_node,
        "recovery_failed": recovery_failed_node,
    }

    for name, body in node_bodies.items():
        builder.add_node(
            name,
            with_resilience(
                body,
                node=name,
                timeout_sec=step_timeout_sec,
                # Mutating and terminal bookkeeping nodes are never auto-retried.
                max_retries=max_retries if name in RETRYABLE_NODES else 0,
                tracer_resolver=_get_tracer,
            ),
        )

    # -- Edges --------------------------------------------------------------
    builder.set_entry_point("triage")
    builder.add_edge("triage", "planner")
    builder.add_edge("planner", "investigator")
    builder.add_edge("investigator", "ops")
    builder.add_edge("ops", "verifier")

    builder.add_conditional_edges(
        "verifier",
        safety_router,
        {
            "executor": "executor",
            "hold_for_approval": "hold_for_approval",
            "policy_rejected": "policy_rejected",
        },
    )

    builder.add_edge("hold_for_approval", END)
    builder.add_edge("policy_rejected", END)

    builder.add_conditional_edges(
        "executor",
        execution_router,
        {"verify_recovery": "verify_recovery", "execution_failed": "execution_failed"},
    )

    builder.add_edge("execution_failed", END)

    builder.add_conditional_edges(
        "verify_recovery",
        recovery_router,
        {"remediation_complete": END, "recovery_failed": "recovery_failed"},
    )
    builder.add_edge("recovery_failed", END)

    if checkpointer is None:
        checkpointer = MemorySaver()

    return builder.compile(checkpointer=checkpointer)
