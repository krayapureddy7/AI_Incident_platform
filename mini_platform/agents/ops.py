"""
Infra / Ops Agent.

Proposes concrete remediation actions expressed as tool invocations.
Synthesizes runbook guidance, records the alternatives it rejected, and derives
the blast radius from the knowledge graph. Exposed as a typed Python agent class
for LangGraph nodes with Pydantic structured I/O.

The Ops Agent has no execution authority: it emits an `ActionProposal` and
nothing more. Dispatch happens only after the Verifier approves and only through
the Tool Gateway.
"""
import time
import uuid
from typing import Any, Dict, List, Optional

from app.tools.contracts import MAX_REPLICA_CEILING

from ..knowledge.knowledge_graph import GLOBAL_KNOWLEDGE_GRAPH, KnowledgeGraph
from ..models import A2AMessage, ActionProposal, AgentRole, AutonomyTier, MessageType
from .base import BaseAgent
from .schemas import OpsInput, OpsOutput


class OpsAgent(BaseAgent):
    """Synthesizes a single, citation-grounded remediation proposal."""

    #: Replica headroom added when relieving CPU/connection saturation.
    SCALE_STEP = 2

    def __init__(self, version: str = "1.3.0", knowledge_graph: Optional[KnowledgeGraph] = None):
        super().__init__(role=AgentRole.OPS, version=version)
        self.kg = knowledge_graph or GLOBAL_KNOWLEDGE_GRAPH

    def run_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the Ops agent as a LangGraph node.

        Validates inputs via `OpsInput`, selects a remediation grounded in the
        retrieved runbook, estimates blast radius via knowledge-graph traversal,
        and returns a validated `OpsOutput`.
        """
        tracer = state.get("tracer")
        start_time = time.time()
        incident = state.get("incident", {})
        evidence = state.get("evidence", {})

        # Pydantic structured input validation.
        OpsInput(incident=incident, evidence=evidence)

        service = incident.get("service", "unknown")
        # The proposal declares the environment/tenant the incident *targets*.
        # It is deliberately not clamped to the session context: the Verifier
        # compares target against session authority and rejects a mismatch as a
        # CROSS_ENVIRONMENT / CROSS_TENANT violation. (Reads differ -- the
        # Investigator scopes those to the session so they never cross at all.)
        env = incident.get("environment", "prod")
        tenant = incident.get("tenant", "default")
        root_cause = evidence.get("identified_root_cause", "UNKNOWN")
        citations = evidence.get("citations", [])
        metrics = evidence.get("metrics", {})

        primary_citation = citations[0]["doc_id"] if citations else "DOC-RB-PAY-001"

        # Blast radius is derived from the knowledge graph, never assumed. The
        # Verifier recomputes it independently; this value documents what the
        # proposing agent believed at proposal time.
        blast_info = self.kg.calculate_blast_radius(service)
        estimated_blast = blast_info.get("total_affected_services", 0)

        rejected_alternatives: List[Dict[str, str]] = []

        if root_cause == "MEMORY_LEAK_HEAP_EXHAUSTION":
            tool_name = "simulate_restart"
            parameters = {
                "reason": (
                    f"Rolling restart to flush leaked JVM heap pool as instructed by "
                    f"{primary_citation}."
                )
            }
            reasoning = (
                f"Service {service} is experiencing "
                f"{metrics.get('memory_utilization_pct')}% memory saturation due to a JVM "
                f"heap leak. Runbook {primary_citation} advises an immediate rolling restart "
                "to flush retained buffers without causing downtime."
            )
            rejected_alternatives = [
                {
                    "alternative": f"simulate_scale({service}, replicas=8)",
                    "reason_rejected": (
                        "Scaling out leaking pods without restarting merely multiplies the "
                        "leaked memory footprint across more instances."
                    ),
                },
                {
                    "alternative": f"simulate_scale({service}, replicas=1)",
                    "reason_rejected": (
                        "Downscaling during a high latency incident violates a safety "
                        "invariant and risks total outage."
                    ),
                },
                {
                    "alternative": "restart_database()",
                    "reason_rejected": (
                        "Database metrics are nominal; the failure is localized to JVM "
                        "application heap."
                    ),
                },
            ]
        elif root_cause == "EVIDENCE_UNAVAILABLE":
            # No telemetry was retrievable, so no mutation can be justified.
            # A read-only re-check is proposed instead; the Verifier classifies
            # this as Tier-1 and it changes nothing.
            tool_name = "get_metrics"
            parameters = {}
            reasoning = (
                f"Evidence collection for {service} was blocked "
                f"({evidence.get('diagnosis_summary')}). No mutating remediation can be "
                "justified without telemetry; proposing a read-only re-check instead."
            )
            rejected_alternatives = [
                {
                    "alternative": f"simulate_restart({service})",
                    "reason_rejected": (
                        "Proposing a mutation without evidence would be an unjustified "
                        "production action."
                    ),
                }
            ]
        else:
            tool_name = "simulate_scale"
            current_replicas = metrics.get("current_replicas") or 2
            target_replicas = min(current_replicas + self.SCALE_STEP, MAX_REPLICA_CEILING)
            parameters = {
                "replicas": target_replicas,
                "reason": (
                    f"Scale replicas from {current_replicas} to {target_replicas} to relieve "
                    "CPU and connection saturation."
                ),
            }
            reasoning = (
                f"Service {service} has high CPU / connection load per runbook "
                f"{primary_citation}. Scaling by +{self.SCALE_STEP} replicas absorbs load "
                "without a cold restart."
            )
            rejected_alternatives = [
                {
                    "alternative": f"simulate_restart({service})",
                    "reason_rejected": (
                        "Cold restart under heavy load causes an immediate connection "
                        "stampede on the remaining pods."
                    ),
                }
            ]

        proposal = ActionProposal(
            action_id=f"ACT-{uuid.uuid4().hex[:8].upper()}",
            tool_name=tool_name,
            service=service,
            environment=env,
            tenant=tenant,
            parameters=parameters,
            reasoning=reasoning,
            evidence_citations=citations,
            estimated_blast_radius=estimated_blast,
            # The Ops Agent proposes the least-privilege tier it believes
            # applies. The Verifier is authoritative and may escalate.
            autonomy_tier=AutonomyTier.TIER_2_VERIFIED_AUTO,
            rejected_alternatives=rejected_alternatives,
        )

        decisions = [
            f"Formulated ActionProposal {proposal.action_id} to invoke '{tool_name}' on "
            f"'{service}'.",
            f"Grounding citation: {primary_citation}.",
            f"Estimated blast radius from knowledge graph: {estimated_blast} affected "
            f"service(s), risk {blast_info.get('risk_rating', 'UNKNOWN')}.",
            "Proposed autonomy tier TIER_2_VERIFIED_AUTO pending Verifier adjudication.",
        ]
        rejected_labels = [
            f"{alt['alternative']}: {alt['reason_rejected']}" for alt in rejected_alternatives
        ]

        duration_ms = (time.time() - start_time) * 1000.0
        if tracer:
            tracer.record_step(
                step_id="STEP-OPS-01",
                workflow_state="PROPOSING_ACTION",
                agent=self.role.value,
                action="PROPOSE_REMEDIATION_ACTION",
                inputs={"service": service, "env": env, "root_cause": root_cause},
                outputs=proposal.to_dict(),
                latency_ms=duration_ms,
                estimated_tokens=410,
                cost_usd=0.00082,
                decisions=decisions,
                rejected_alternatives=rejected_labels,
            )

        # Pydantic structured output validation.
        validated_output = OpsOutput(proposal=proposal.to_dict(), ops_version=self.version)

        return {"proposal": validated_output.proposal, "current_state": "PROPOSING_ACTION"}

    def handle_message(self, message: A2AMessage, context: Dict[str, Any]) -> A2AMessage:
        node_res = self.run_node(
            {
                "incident": message.payload.get("incident", {}),
                "evidence": message.payload.get("evidence", {}),
                "env_context": context.get("env_context"),
                "tenant_context": context.get("tenant_context"),
                "tracer": context.get("tracer"),
            }
        )
        return self.create_message(
            recipient=AgentRole.VERIFIER,
            message_type=MessageType.ACTION_PROPOSAL,
            correlation_id=message.correlation_id,
            payload={
                "incident": message.payload.get("incident", {}),
                "proposal": node_res["proposal"],
                "ops_version": self.version,
            },
        )
