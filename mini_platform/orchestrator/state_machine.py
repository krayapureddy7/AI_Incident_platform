"""
LangGraph Incident Orchestrator for Multi-Agent Incident Remediation.

Uses LangGraph StateGraph as the sole agent orchestration/runtime framework.
Manages state transitions, retry and timeout safeguards, durable run
persistence, and audit logging.

Tool topology
-------------
The orchestrator owns exactly one tool implementation (bound to one simulated
cluster) and exactly one Tool Gateway in front of it. Agents receive the gateway,
never the implementation, so there is a single execution path and a single source
of infrastructure state.
"""
import uuid
from typing import Any, Dict, Optional

from app.tools.client import ToolClient
from app.tools.gateway import ToolGateway
from app.tools.server import DEFAULT_TOOL_IMPL, InfraToolServer, build_fastmcp_server

from ..agents.investigator import InvestigatorAgent
from ..agents.ops import OpsAgent
from ..agents.planner import PlannerAgent
from ..agents.verifier import VerifierAgent
from ..models import Incident, WorkflowState
from ..llm import LLMProvider, resolve_provider
from ..persistence.store import IncidentStore
from ..tracing.tracer import AuditTracer
from .checkpointing import build_checkpointer, close_checkpointer
from .langgraph_workflow import IncidentGraphState, build_incident_graph
from .resilience import NodeExecutionError

__all__ = ["IncidentOrchestrator", "OrchestrationException"]


class OrchestrationException(Exception):
    """Raised for unrecoverable orchestration faults (e.g. missing checkpoint)."""


class IncidentOrchestrator:
    """
    LangGraph-based incident orchestrator controlling the multi-agent remediation
    workflow.

    All agents (Planner, Investigator, Ops, Verifier) execute as typed nodes with
    Pydantic structured I/O. Nodes carry retry and timeout budgets, and workflow
    state is checkpointed so a Tier-3 hold can be resumed after human approval.
    """

    def __init__(
        self,
        tool_server: Optional[InfraToolServer] = None,
        tool_gateway: Optional[ToolGateway] = None,
        max_retries: int = 2,
        step_timeout_sec: float = 15.0,
        checkpointer: Optional[Any] = None,
        store: Optional[IncidentStore] = None,
        checkpoint_db: Optional[str] = None,
        llm_provider: Optional[LLMProvider] = None,
    ):
        self.tool_server = tool_server or DEFAULT_TOOL_IMPL

        # A gateway bound to this orchestrator's cluster. Supplying an explicit
        # gateway is honoured; otherwise one is constructed over the tool
        # implementation so reads, writes, and post-checks all observe the same
        # cluster without any manual state synchronisation.
        self.tool_gateway = tool_gateway or ToolGateway(
            client=ToolClient(build_fastmcp_server(self.tool_server))
        )

        self.max_retries = max_retries
        self.step_timeout_sec = step_timeout_sec
        # An explicit checkpointer wins; otherwise a durable SQLite checkpointer
        # is built when `checkpoint_db` is supplied, and an in-memory one when
        # it is not (tests and one-shot runs).
        self.checkpointer = checkpointer or build_checkpointer(checkpoint_db)
        self.store = store

        # Reasoning backend, resolved once and shared by every agent. It is held
        # here rather than in graph state on purpose: LangGraph checkpoints the
        # whole state dict, and a live HTTP client is not serializable. Nodes
        # reach it through this orchestrator, the same way they reach the gateway.
        self.llm_provider = (
            llm_provider if llm_provider is not None else resolve_provider()
        )

        # Typed agents exposed as LangGraph nodes.
        self.planner = PlannerAgent(llm_provider=self.llm_provider)
        self.investigator = InvestigatorAgent(
            tool_gateway=self.tool_gateway, llm_provider=self.llm_provider
        )
        self.ops = OpsAgent(llm_provider=self.llm_provider)
        self.verifier = VerifierAgent(llm_provider=self.llm_provider)

        self.graph = build_incident_graph(
            self,
            checkpointer=self.checkpointer,
            max_retries=self.max_retries,
            step_timeout_sec=self.step_timeout_sec,
        )

    # -- Lifecycle ----------------------------------------------------------
    def close(self) -> None:
        """
        Release resources held by the orchestrator.

        Closes the checkpointer's database handle and any client the reasoning
        provider holds. Safe to call more than once, and a no-op for in-memory
        checkpointing with no provider configured.
        """
        close_checkpointer(self.checkpointer)
        if self.llm_provider is not None:
            self.llm_provider.close()

    def __enter__(self) -> "IncidentOrchestrator":
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.close()

    # -- Execution ----------------------------------------------------------
    def run_incident(
        self,
        incident: Incident,
        human_approval_token: Optional[str] = None,
        env_context: str = "prod",
        run_id: Optional[str] = None,
        tenant_context: str = "default",
    ) -> Dict[str, Any]:
        """
        Execute the incident remediation workflow through the LangGraph StateGraph.

        Returns a structured result describing the terminal state, the proposal,
        the safety verdict, execution outcome, and the audit trace.
        """
        if not run_id:
            run_id = f"RUN-{uuid.uuid4().hex[:8].upper()}"
        tracer = AuditTracer(run_id=run_id)

        incident_dict = incident.to_dict() if hasattr(incident, "to_dict") else dict(incident)

        initial_state: IncidentGraphState = {
            "run_id": run_id,
            "incident": incident_dict,
            "env_context": env_context,
            "tenant_context": tenant_context,
            "human_approval_token": human_approval_token,
            "tracer": None,
            "current_state": WorkflowState.IDLE.value,
            "workflow_status": "RUNNING",
            "status_reason": None,
            "last_a2a_message": None,
            "a2a_messages": [],
            "plan": None,
            "evidence": None,
            "proposal": None,
            "safety_result": None,
            "before_metrics": None,
            "before_logs": None,
            "selected_tool_version": None,
            "execution_result": None,
            "post_check": None,
            "recovery_verification": None,
            "trace_file": None,
        }

        config = {"configurable": {"thread_id": run_id}}
        try:
            final_state = self.graph.invoke(initial_state, config=config)
        except NodeExecutionError as exc:
            return self._orchestration_failure(run_id, incident_dict, env_context, tracer, exc)

        return self._format_response(run_id, final_state, incident_dict, env_context, tracer)

    def resume_incident(self, run_id: str, human_approval_token: str) -> Dict[str, Any]:
        """
        Resume a workflow halted at ``AWAITING_APPROVAL`` using its checkpoint.

        Injects the human approval token and continues execution through the
        safety verifier to completion.
        """
        config = {"configurable": {"thread_id": run_id}}
        checkpoint_state = self.graph.get_state(config)
        if not checkpoint_state or not checkpoint_state.values:
            raise OrchestrationException(f"No checkpoint found for run_id '{run_id}'")

        # Re-enter at the Verifier so the approved proposal is re-adjudicated
        # against the full policy set rather than being executed on trust.
        self.graph.update_state(
            config,
            {
                "human_approval_token": human_approval_token,
                "workflow_status": "RESUMED_PENDING_VERIFICATION",
            },
            as_node="ops",
        )

        tracer = AuditTracer.get(run_id) or AuditTracer(run_id=run_id)
        try:
            final_state = self.graph.invoke(None, config=config)
        except NodeExecutionError as exc:
            incident_dict = checkpoint_state.values.get("incident", {})
            env_context = checkpoint_state.values.get("env_context", "prod")
            return self._orchestration_failure(run_id, incident_dict, env_context, tracer, exc)

        incident_dict = final_state.get("incident", {})
        env_context = final_state.get("env_context", "prod")
        return self._format_response(run_id, final_state, incident_dict, env_context, tracer)

    def get_checkpoint_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve the persisted state snapshot for a given incident run."""
        config = {"configurable": {"thread_id": run_id}}
        snapshot = self.graph.get_state(config)
        if not snapshot or not snapshot.values:
            return None
        return {"values": snapshot.values, "next": snapshot.next, "config": snapshot.config}

    # -- Response assembly --------------------------------------------------
    def _persist(self, run_id: str, incident_dict: Dict[str, Any], response: Dict[str, Any]) -> None:
        """Write the run record and trace to the durable store, when configured."""
        if self.store is None:
            return
        self.store.record_run(run_id, incident_dict, response)
        if response.get("trace"):
            self.store.record_trace(run_id, response["trace"])

    def _orchestration_failure(
        self,
        run_id: str,
        incident_dict: Dict[str, Any],
        env_context: str,
        tracer: Optional[AuditTracer],
        exc: NodeExecutionError,
    ) -> Dict[str, Any]:
        """
        Build a terminal response for a node that exhausted its retry budget.

        The trace is persisted so an operator can see exactly which node failed,
        how many attempts were made, and why.
        """
        trace_file = None
        if tracer:
            tracer.record_transition(
                WorkflowState.IDLE.value,
                WorkflowState.FAILED.value,
                "ORCHESTRATION_NODE_FAILURE",
                {"node": exc.node, "attempts": exc.attempts, "error": str(exc.last_error)},
            )
            trace_file = tracer.persist()

        response = {
            "run_id": run_id,
            "final_state": WorkflowState.FAILED.value,
            "status": "ORCHESTRATION_FAILED",
            "workflow_status": "ORCHESTRATION_FAILED",
            "reason": str(exc),
            "failed_node": exc.node,
            "attempts": exc.attempts,
            "incident": incident_dict,
            "a2a_messages": [],
            "trace": tracer.export_trace() if tracer else None,
            "trace_file": trace_file,
        }
        self._persist(run_id, incident_dict, response)
        return response

    def _format_response(
        self,
        run_id: str,
        final_state: Dict[str, Any],
        incident_dict: Dict[str, Any],
        env_context: str,
        tracer: Optional[AuditTracer],
    ) -> Dict[str, Any]:
        """Project terminal graph state into the public result contract."""
        workflow_status = final_state.get("workflow_status")
        trace = tracer.export_trace() if tracer else None

        response: Dict[str, Any] = {
            "run_id": run_id,
            "final_state": final_state.get("current_state", WorkflowState.FAILED.value),
            "status": workflow_status,
            "workflow_status": workflow_status,
            "incident": incident_dict,
            "a2a_messages": final_state.get("a2a_messages", []),
            "trace": trace,
            "trace_file": final_state.get("trace_file"),
        }

        if workflow_status in ("BLOCKED_FOR_APPROVAL", "REJECTED"):
            response.update(
                {
                    "reason": final_state.get("status_reason"),
                    "proposal": final_state.get("proposal"),
                    "safety_result": final_state.get("safety_result"),
                }
            )
        elif workflow_status == "RECOVERY_VERIFICATION_FAILED":
            response.update(
                {
                    "reason": final_state.get("status_reason"),
                    "proposal": final_state.get("proposal"),
                    "safety_result": final_state.get("safety_result"),
                    "execution_result": final_state.get("execution_result"),
                    "post_recovery_metrics": final_state.get("post_check"),
                    "recovery_verification": final_state.get("recovery_verification"),
                }
            )
        elif workflow_status == "EXECUTION_FAILED":
            response.update(
                {
                    "reason": final_state.get("status_reason"),
                    "proposal": final_state.get("proposal"),
                    "safety_result": final_state.get("safety_result"),
                    "execution_result": final_state.get("execution_result"),
                }
            )
        else:  # RESOLVED
            response.update(
                {
                    "status": "RESOLVED",
                    "workflow_status": "RESOLVED",
                    "proposal": final_state.get("proposal"),
                    "safety_result": final_state.get("safety_result"),
                    "execution_result": final_state.get("execution_result"),
                    "selected_tool_version": final_state.get("selected_tool_version"),
                    "post_recovery_metrics": final_state.get("post_check"),
                    "recovery_verification": final_state.get("recovery_verification"),
                    "replay_metadata": {
                        "run_id": run_id,
                        "tool_version": final_state.get("selected_tool_version"),
                        "environment": env_context,
                        "trace_file": final_state.get("trace_file"),
                    },
                }
            )

        self._persist(run_id, incident_dict, response)
        return response
