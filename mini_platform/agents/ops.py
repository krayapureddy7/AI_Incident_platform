"""
Infra / Ops Agent.

Proposes concrete remediation actions expressed as tool invocations.
Synthesizes runbook guidance, records the alternatives it rejected, and derives
the blast radius from the knowledge graph. Exposed as a typed Python agent class
for LangGraph nodes with Pydantic structured I/O.

The Ops Agent has no execution authority: it emits an `ActionProposal` and
nothing more. Dispatch happens only after the Verifier approves and only through
the Tool Gateway.

Reasoning
---------
The remediation may be chosen by a language model. What the model emits is a
suggestion, not an instruction: the tool name is checked against the published
catalog, the service against the knowledge graph, and the whole proposal is then
adjudicated by the deterministic safety engine, which recomputes blast radius and
tier independently. A model cannot name a tool that does not exist, target a
service the platform does not know, mint its own action id, or fabricate the
citations attached to its proposal.
"""
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.tools.contracts import MAX_REPLICA_CEILING, MUTATING_TOOLS, READ_ONLY_TOOLS

from ..knowledge.knowledge_graph import GLOBAL_KNOWLEDGE_GRAPH, KnowledgeGraph
from ..llm import LLMProvider, resolve_provider, trace_kwargs
from ..llm.prompts import OPS_PROMPT_VERSION, ops_prompt
from ..models import A2AMessage, ActionProposal, AgentRole, AutonomyTier, MessageType
from .base import BaseAgent
from .schemas import LLMRemediationProposal, OpsInput, OpsOutput

#: Per-step usage recorded when reasoning deterministically.
DETERMINISTIC_TOKENS = 410
DETERMINISTIC_COST_USD = 0.00082

#: Every tool a proposal may name. A generated name outside this set is a
#: hallucination and the proposal is discarded before it reaches the gateway.
PROPOSABLE_TOOLS = frozenset(READ_ONLY_TOOLS | MUTATING_TOOLS)


class OpsAgent(BaseAgent):
    """Synthesizes a single, citation-grounded remediation proposal."""

    #: Replica headroom added when relieving CPU/connection saturation.
    SCALE_STEP = 2

    def __init__(
        self,
        version: str = "1.4.0",
        knowledge_graph: Optional[KnowledgeGraph] = None,
        llm_provider: Optional[LLMProvider] = None,
    ):
        super().__init__(role=AgentRole.OPS, version=version)
        self.kg = knowledge_graph or GLOBAL_KNOWLEDGE_GRAPH
        self.llm = llm_provider if llm_provider is not None else resolve_provider()

    # -- Remediation selection ---------------------------------------------
    def _deterministic_proposal(
        self,
        service: str,
        root_cause: str,
        evidence: Dict[str, Any],
        metrics: Dict[str, Any],
        primary_citation: str,
    ) -> Tuple[str, Dict[str, Any], str, List[Dict[str, str]]]:
        """
        Rule-based remediation selection.

        The fallback path and the offline default. Returns
        ``(tool_name, parameters, reasoning, rejected_alternatives)``.
        """
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

        return tool_name, parameters, reasoning, rejected_alternatives

    def _select_remediation(
        self,
        service: str,
        env: str,
        root_cause: str,
        evidence: Dict[str, Any],
        metrics: Dict[str, Any],
        citations: List[Dict[str, Any]],
        primary_citation: str,
    ) -> Tuple[Tuple[str, Dict[str, Any], str, List[Dict[str, str]]], Dict[str, Any], str]:
        """
        Choose the remediation, by model where available and by rules otherwise.

        Returns ``((tool_name, parameters, reasoning, rejected), trace_kwargs, mode)``.

        A generation is discarded in favour of the deterministic branch when it
        fails schema validation, names a tool outside the published catalog, or
        targets a service absent from the knowledge graph. Those two checks are
        what stop a fabricated action reaching the Tool Gateway at all -- the
        gateway would reject it anyway, but a rejection there terminates the
        workflow, whereas falling back here still remediates the incident.
        """
        fallback = lambda: self._deterministic_proposal(  # noqa: E731
            service, root_cause, evidence, metrics, primary_citation
        )

        if self.llm is None:
            return (
                fallback(),
                trace_kwargs("deterministic", None, DETERMINISTIC_TOKENS, DETERMINISTIC_COST_USD),
                "deterministic",
            )

        result = self.llm.complete(
            prompt=ops_prompt(
                service=service,
                environment=env,
                root_cause=root_cause,
                diagnosis_summary=evidence.get("diagnosis_summary") or "",
                metrics=metrics,
                citations=citations,
                permitted_tools=sorted(PROPOSABLE_TOOLS),
                max_replicas=MAX_REPLICA_CEILING,
            ),
            schema=LLMRemediationProposal,
            purpose="ops_remediation",
            prompt_version=OPS_PROMPT_VERSION,
        )

        mode = "llm"
        if not result.valid or result.parsed is None:
            mode = "fallback:invalid_output"
        elif result.parsed.tool_name not in PROPOSABLE_TOOLS:
            mode = "fallback:unknown_tool"
        elif self.kg.get_service(service) is None:
            mode = "fallback:unknown_service"

        if mode != "llm":
            return (
                fallback(),
                trace_kwargs(mode, result, DETERMINISTIC_TOKENS, DETERMINISTIC_COST_USD),
                mode,
            )

        proposed = result.parsed
        # Scope-bearing fields are supplied by the platform, never by the model:
        # they are what the environment and tenant barriers compare against.
        parameters = {
            k: v
            for k, v in proposed.parameters.items()
            if k not in {"service", "environment", "tenant"}
        }
        rejected = [alt.model_dump() for alt in proposed.rejected_alternatives]
        return (
            (proposed.tool_name, parameters, proposed.reasoning, rejected),
            trace_kwargs("llm", result, DETERMINISTIC_TOKENS, DETERMINISTIC_COST_USD),
            "llm",
        )

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

        (
            tool_name,
            parameters,
            reasoning,
            rejected_alternatives,
        ), llm_trace, mode = self._select_remediation(
            service=service,
            env=env,
            root_cause=root_cause,
            evidence=evidence,
            metrics=metrics,
            citations=citations,
            primary_citation=primary_citation,
        )

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
        if mode != "deterministic":
            decisions.append(f"Remediation selection reasoning mode: {mode}.")
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
                decisions=decisions,
                rejected_alternatives=rejected_labels,
                **llm_trace,
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
