"""
Incident Remediation HTTP API.

A thin, typed FastAPI surface over the same orchestrator, Tool Gateway, and
knowledge components the CLI uses. The API adds no policy of its own: every
mutating action still travels the Planner -> Investigator -> Ops -> Verifier ->
Tool Gateway path, and Tier-3 actions still halt for a human approval token.

Run locally:
    uvicorn mini_platform.api.server:app --reload --port 8000
"""
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.tools.contracts import TOOL_VERSIONS

from ..evals.benchmark_scenarios import BENCHMARK_SCENARIOS
from ..knowledge.hybrid_rag import GLOBAL_HYBRID_RAG
from ..knowledge.knowledge_graph import GLOBAL_KNOWLEDGE_GRAPH
from ..models import ActionProposal, AutonomyTier, Incident
from ..orchestrator.checkpointing import DEFAULT_CHECKPOINT_DB
from ..orchestrator.state_machine import IncidentOrchestrator, OrchestrationException
from ..persistence.store import IncidentStore
from ..safety.guardrails import GLOBAL_SAFETY_GUARDRAILS
from ..tracing.tracer import TraceReplayer, redact_sensitive_data

API_VERSION = "1.1.0"
PLATFORM_VERSION = "1.4.0"

#: Session authority for this deployment.
#
# The environment and tenant barriers compare the *proposal's* target against
# the *session's* authority. Both sides therefore cannot come from the same
# request: a caller who supplies both can only ever compare a value to itself,
# which makes the barrier unfalsifiable. Authority is deployment configuration,
# so a prod-scoped API refuses to act on a staging incident no matter what the
# request body claims.
DEFAULT_SESSION_ENVIRONMENT = os.environ.get("SESSION_ENVIRONMENT", "prod")
DEFAULT_SESSION_TENANT = os.environ.get("SESSION_TENANT", "default")

#: Origins allowed to call this API from a browser. Comma-separated env var so
#: a deployment can widen it without a code change; defaults to the local
#: frontend dev server only, never a wildcard, since responses can carry
#: incident detail and audit traces.
DEFAULT_ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def _configured_origins() -> List[str]:
    """Read CORS origins from the environment, falling back to the dev default."""
    configured = os.environ.get("CORS_ALLOWED_ORIGINS")
    if configured is None:
        return list(DEFAULT_ALLOWED_ORIGINS)
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


# ---------------------------------------------------------------------------
# Request / Response contracts
# ---------------------------------------------------------------------------
class IncidentRequest(BaseModel):
    """Incident submission payload."""

    title: str = Field(..., min_length=1, description="Short incident title")
    description: str = Field(..., min_length=1, description="Alert text / incident description")
    service: str = Field(..., min_length=1, description="Affected service name")
    incident_id: Optional[str] = Field(default=None, description="Caller-supplied incident id")
    environment: str = Field(default="prod", description="Target environment")
    tenant: str = Field(default="default", description="Target tenant")
    severity: str = Field(default="SEV-1", description="Incident severity")
    human_approval_token: Optional[str] = Field(
        default=None,
        description=(
            "Signed Tier-3 approval token, bound to the action it authorizes. "
            "Only usable for a proposal that already exists, so it cannot "
            "pre-approve the action this request is about to create."
        ),
    )


class ApprovalRequest(BaseModel):
    """Human approval payload for a run halted at AWAITING_APPROVAL."""

    human_approval_token: str = Field(
        ..., min_length=1, description="Human approval token authorizing a Tier-3 action"
    )


class KnowledgeQueryRequest(BaseModel):
    """Hybrid RAG query payload."""

    query: str = Field(..., min_length=1, description="Natural-language search query")
    service: Optional[str] = Field(default=None, description="Service metadata filter")
    environment: Optional[str] = Field(default=None, description="Environment metadata filter")
    doc_type: Optional[str] = Field(default=None, description="Document type metadata filter")
    top_k: int = Field(default=3, ge=1, le=10, description="Maximum results to return")


class ToolInvokeRequest(BaseModel):
    """Direct invocation payload for a read-only tool."""

    service: str = Field(..., min_length=1, description="Target service")
    parameters: Dict[str, Any] = Field(
        default_factory=dict, description="Extra tool parameters, e.g. timeframe or limit"
    )


class SafetyPreviewRequest(BaseModel):
    """
    A hypothetical action to run through the deterministic policy engine.

    Read-only: this evaluates the same ``SafetyGuardrails.evaluate_proposal``
    the real remediation path uses, but nothing here reaches the Tool Gateway,
    so no infrastructure action is ever dispatched from this endpoint.
    """

    tool_name: str = Field(..., min_length=1, description="Tool to evaluate, e.g. simulate_restart")
    service: str = Field(..., min_length=1, description="Target service")
    environment: str = Field(default="prod", description="Proposal's target environment")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Tool parameters")
    human_approval_token: Optional[str] = Field(
        default=None, description="Approval token to test against this hypothetical action"
    )


class RedactionPreviewRequest(BaseModel):
    """Arbitrary payload to run through the trace redaction pipeline."""

    data: Dict[str, Any] = Field(..., description="JSON object to redact")


class HealthResponse(BaseModel):
    status: str
    platform_version: str
    api_version: str
    persisted_runs: int
    session_environment: str = Field(
        ..., description="Environment this deployment is authorized to act on"
    )
    session_tenant: str = Field(
        ..., description="Tenant this deployment is authorized to act on"
    )


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------
def create_app(
    orchestrator: Optional[IncidentOrchestrator] = None,
    store: Optional[IncidentStore] = None,
    session_environment: Optional[str] = None,
    session_tenant: Optional[str] = None,
    allowed_origins: Optional[List[str]] = None,
) -> FastAPI:
    """
    Build the FastAPI application.

    Dependencies are injectable so tests can supply an isolated cluster and a
    temporary database rather than sharing process-wide state.

    Args:
        session_environment: Environment this deployment is authorized to act
            on. Incidents targeting any other environment are rejected by the
            safety engine. Never taken from the request body.
        session_tenant: Tenant authority for this deployment, on the same terms.
        allowed_origins: Browser origins permitted to call this API. Defaults to
            the local frontend dev server; production deployments should set
            ``CORS_ALLOWED_ORIGINS`` rather than widen this to a wildcard, since
            responses can carry incident detail and audit traces.
    """
    env_authority = session_environment or DEFAULT_SESSION_ENVIRONMENT
    tenant_authority = session_tenant or DEFAULT_SESSION_TENANT
    origins = allowed_origins if allowed_origins is not None else _configured_origins()
    incident_store = store or IncidentStore()
    engine = orchestrator or IncidentOrchestrator(
        store=incident_store, checkpoint_db=DEFAULT_CHECKPOINT_DB
    )

    app = FastAPI(
        title="Mini Agentic AI Platform - Incident Remediation API",
        description=(
            "Multi-agent incident analysis and remediation with deterministic "
            "guardrails, hybrid retrieval, and replayable audit traces."
        ),
        version=API_VERSION,
    )
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )
    app.state.orchestrator = engine
    app.state.store = incident_store
    app.state.session_environment = env_authority
    app.state.session_tenant = tenant_authority

    # -- Operational ----------------------------------------------------
    @app.get("/health", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        """Liveness probe with persistence reachability."""
        return HealthResponse(
            status="ok",
            platform_version=PLATFORM_VERSION,
            api_version=API_VERSION,
            persisted_runs=incident_store.count_runs(),
            session_environment=env_authority,
            session_tenant=tenant_authority,
        )

    @app.get("/tools", tags=["tools"])
    def list_tools() -> Dict[str, Any]:
        """Published tool catalog with versions and JSON-Schema contracts."""
        return {
            "tool_versions": TOOL_VERSIONS,
            "tools": engine.tool_server.tool_catalog(),
        }

    @app.post("/tools/{tool_name}/invoke", tags=["tools"])
    def invoke_read_only_tool(tool_name: str, payload: ToolInvokeRequest) -> Dict[str, Any]:
        """
        Dispatch a read-only tool directly, outside the incident workflow.

        Lets the tool catalog be explored with live data. ``ToolGateway.
        execute_read_only`` rejects anything not in ``READ_ONLY_TOOLS`` before
        dispatch, so this can never reach ``simulate_restart`` / ``simulate_scale``
        -- mutation only ever happens through the full Planner -> Investigator ->
        Ops -> Verifier path, never from a direct tool call.
        """
        result = engine.tool_gateway.execute_read_only(
            tool_name=tool_name,
            service=payload.service,
            environment=env_authority,
            tenant=tenant_authority,
            extra_params=payload.parameters,
        )
        if not result.get("ok") and result.get("error", {}).get("code") == "POLICY_DENIED":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=result["error"])
        return result

    # -- Incident lifecycle ---------------------------------------------
    @app.post("/incidents", status_code=status.HTTP_201_CREATED, tags=["incidents"])
    def submit_incident(payload: IncidentRequest) -> Dict[str, Any]:
        """
        Analyze and remediate an incident.

        Returns the terminal workflow state. A Tier-3 action halts with
        ``BLOCKED_FOR_APPROVAL``; call the approval endpoint to continue.
        """
        incident = Incident(
            id=payload.incident_id or f"API-{payload.service.upper()}",
            title=payload.title,
            description=payload.description,
            service=payload.service,
            environment=payload.environment,
            tenant=payload.tenant,
            severity=payload.severity,
        )
        return engine.run_incident(
            incident=incident,
            human_approval_token=payload.human_approval_token,
            env_context=env_authority,
            tenant_context=tenant_authority,
        )

    @app.post("/incidents/{run_id}/approve", tags=["incidents"])
    def approve_incident(run_id: str, payload: ApprovalRequest) -> Dict[str, Any]:
        """Supply a human approval token and resume a halted Tier-3 workflow."""
        try:
            return engine.resume_incident(
                run_id=run_id, human_approval_token=payload.human_approval_token
            )
        except OrchestrationException as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc

    @app.get("/incidents", tags=["incidents"])
    def list_incidents(
        limit: int = Query(default=50, ge=1, le=200),
        service: Optional[str] = None,
        run_status: Optional[str] = Query(default=None, alias="status"),
    ) -> Dict[str, Any]:
        """List persisted runs, newest first."""
        runs = incident_store.list_runs(limit=limit, service=service, status=run_status)
        return {"count": len(runs), "runs": runs}

    @app.get("/incidents/{run_id}", tags=["incidents"])
    def get_incident(run_id: str) -> Dict[str, Any]:
        """Fetch a persisted run record."""
        run = incident_store.get_run(run_id)
        if not run:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Run '{run_id}' not found."
            )
        return run

    @app.get("/incidents/{run_id}/state", tags=["incidents"])
    def get_incident_state(run_id: str) -> Dict[str, Any]:
        """Inspect the live LangGraph checkpoint for an in-process run."""
        snapshot = engine.get_checkpoint_state(run_id)
        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No active checkpoint for run '{run_id}'.",
            )
        return {
            "run_id": run_id,
            "current_state": snapshot["values"].get("current_state"),
            "workflow_status": snapshot["values"].get("workflow_status"),
            "next_nodes": list(snapshot["next"] or []),
        }

    # -- Observability ---------------------------------------------------
    @app.get("/traces/{run_id}", tags=["observability"])
    def get_trace(run_id: str) -> Dict[str, Any]:
        """Fetch the redacted audit trace for a run."""
        trace = incident_store.get_trace(run_id)
        if not trace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No trace persisted for run '{run_id}'.",
            )
        return trace

    @app.get("/traces/{run_id}/replay", tags=["observability"])
    def replay_trace(run_id: str) -> Dict[str, Any]:
        """Render a human-readable post-mortem replay of a run."""
        trace = incident_store.get_trace(run_id)
        if not trace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No trace persisted for run '{run_id}'.",
            )
        return {"run_id": run_id, "replay": TraceReplayer.replay_summary(trace)}

    # -- Knowledge -------------------------------------------------------
    @app.post("/knowledge/search", tags=["knowledge"])
    def search_knowledge(payload: KnowledgeQueryRequest) -> Dict[str, Any]:
        """Hybrid retrieval (BM25 + dense projection, RRF fused) with metadata filters."""
        hits = GLOBAL_HYBRID_RAG.search(
            query=payload.query,
            service_filter=payload.service,
            env_filter=payload.environment,
            type_filter=payload.doc_type,
            top_k=payload.top_k,
        )
        return {"query": payload.query, "count": len(hits), "results": hits}

    @app.get("/services/{service}/blast-radius", tags=["knowledge"])
    def blast_radius(service: str) -> Dict[str, Any]:
        """Knowledge-graph BFS blast-radius assessment for a service."""
        analysis = GLOBAL_KNOWLEDGE_GRAPH.calculate_blast_radius(service)
        if not analysis.get("found"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Service '{service}' is not present in the knowledge graph.",
            )
        return analysis

    @app.get("/services", tags=["knowledge"])
    def list_services() -> Dict[str, Any]:
        """Service topology as nodes and dependency edges."""
        return GLOBAL_KNOWLEDGE_GRAPH.get_full_graph_data()

    # -- Safety (read-only preview) ---------------------------------------
    @app.post("/safety/preview", tags=["safety"])
    def preview_safety(payload: SafetyPreviewRequest) -> Dict[str, Any]:
        """
        Evaluate a hypothetical action against the live policy engine.

        Runs the exact ``SafetyGuardrails.evaluate_proposal`` the real
        remediation path uses, so the tier, blast radius, and approval
        decisions shown here are real -- not a re-implementation. Nothing here
        reaches the Tool Gateway: a proposal built for a preview has no
        ``action_id`` in the store, so even an approved-looking result cannot
        be replayed into an execution.
        """
        proposal = ActionProposal(
            action_id=f"PREVIEW-{payload.tool_name}-{payload.service}",
            tool_name=payload.tool_name,
            service=payload.service,
            environment=payload.environment,
            tenant=tenant_authority,
            parameters=payload.parameters,
            reasoning="Safety preview (read-only, not executed)",
            evidence_citations=[],
            estimated_blast_radius=0,
            autonomy_tier=AutonomyTier.TIER_2_VERIFIED_AUTO,
        )
        result = GLOBAL_SAFETY_GUARDRAILS.evaluate_proposal(
            proposal,
            env_context=env_authority,
            human_approval_token=payload.human_approval_token,
            tenant_context=tenant_authority,
        )
        return {
            "approved": result.approved,
            "tier": result.tier.name,
            "requires_human_token": result.requires_human_token,
            "policy_violations": result.policy_violations,
            "blast_radius_analysis": result.blast_radius_analysis,
            "explanation": result.explanation,
            "checks_performed": result.checks_performed,
        }

    @app.post("/safety/redact-preview", tags=["safety"])
    def preview_redaction(payload: RedactionPreviewRequest) -> Dict[str, Any]:
        """Run arbitrary JSON through the same redaction pipeline traces use."""
        return {"redacted": redact_sensitive_data(payload.data)}

    # -- Evaluation gate -----------------------------------------------------
    @app.post("/eval/run", tags=["evaluation"])
    def run_evaluation() -> Dict[str, Any]:
        """
        Execute the offline benchmark suite and return structured results.

        Each scenario builds its own isolated orchestrator over a private mock
        cluster (see ``EvaluationSuite._build_orchestrator``), so this is safe
        to invoke repeatedly and never touches the deployment's real store or
        checkpoint database.
        """
        from ..evals.eval_runner import EvaluationSuite

        return EvaluationSuite().run_all()

    @app.get("/eval/scenarios", tags=["evaluation"])
    def list_evaluation_scenarios() -> Dict[str, Any]:
        """Published benchmark scenario catalog, for display before running."""
        return {
            "count": len(BENCHMARK_SCENARIOS),
            "scenarios": [
                {
                    "id": sc["id"],
                    "name": sc["name"],
                    "service": sc["incident"].service,
                    "environment": sc["incident"].environment,
                    "title": sc["incident"].title,
                    "description": sc["incident"].description,
                    "severity": sc["incident"].severity,
                    "expected": sc["expected_trajectory"],
                }
                for sc in BENCHMARK_SCENARIOS
            ],
        }

    return app


#: ASGI application used by uvicorn and the container image.
app = create_app()
