"""
Verifier / Safety Agent: Enforces policies, blast-radius boundaries, and autonomy tiers.
Runs deterministic policy checks before any mutating tool execution.
Exposed as a typed Python agent class for LangGraph nodes with Pydantic structured I/O.

Reasoning
---------
This agent is hybrid in a deliberately lopsided way. The verdict -- approved,
tier, whether a human must sign off -- comes from `SafetyGuardrails` and nothing
else. A language model, where configured, is asked afterwards to put that verdict
into plain language for the on-call engineer.

The narrative is advisory by construction, not by policy: it is generated after
`evaluate_proposal` has returned, it is never read back into the decision, and the
graph's routing predicate reads only `approved` and `requires_human_token`. A
model cannot approve an action here, and a prompt-injected log line cannot argue
its way past a blast-radius limit, because nothing it emits is an input to the
verdict.
"""
import time
from typing import Any, Dict, Optional, Tuple
from .base import BaseAgent
from .schemas import LLMRiskNarrative, VerifierInput, VerifierOutput
from ..llm import LLMProvider, resolve_provider, trace_kwargs
from ..llm.prompts import VERIFIER_PROMPT_VERSION, verifier_prompt
from ..models import AgentRole, A2AMessage, MessageType, ActionProposal, AutonomyTier
from ..safety.guardrails import GLOBAL_SAFETY_GUARDRAILS, SafetyGuardrails

#: Per-step usage recorded when reasoning deterministically.
DETERMINISTIC_TOKENS = 380
DETERMINISTIC_COST_USD = 0.00076


class VerifierAgent(BaseAgent):
    def __init__(
        self,
        version: str = "1.5.0",
        guardrails: SafetyGuardrails = None,
        llm_provider: Optional[LLMProvider] = None,
    ):
        super().__init__(role=AgentRole.VERIFIER, version=version)
        self.guardrails = guardrails or GLOBAL_SAFETY_GUARDRAILS
        self.llm = llm_provider if llm_provider is not None else resolve_provider()

    def _narrate(
        self, proposal_dict: Dict[str, Any], safety_result_dict: Dict[str, Any]
    ) -> Tuple[Optional[str], Dict[str, Any], str]:
        """
        Explain a verdict that has already been reached.

        Returns ``(narrative, trace_kwargs, mode)``. Failure is uneventful: with
        no narrative the run proceeds on the same verdict it would have used
        anyway, because the verdict was never the model's to produce.
        """
        if self.llm is None:
            return (
                None,
                trace_kwargs("deterministic", None, DETERMINISTIC_TOKENS, DETERMINISTIC_COST_USD),
                "deterministic",
            )

        result = self.llm.complete(
            prompt=verifier_prompt(proposal_dict, safety_result_dict),
            schema=LLMRiskNarrative,
            purpose="verifier_narrative",
            prompt_version=VERIFIER_PROMPT_VERSION,
        )
        if not result.valid or result.parsed is None:
            mode = "fallback:invalid_output"
            return (
                None,
                trace_kwargs(mode, result, DETERMINISTIC_TOKENS, DETERMINISTIC_COST_USD),
                mode,
            )
        return (
            result.parsed.risk_narrative,
            trace_kwargs("llm", result, DETERMINISTIC_TOKENS, DETERMINISTIC_COST_USD),
            "llm",
        )

    def run_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute Verifier agent as a LangGraph node.
        Accepts state, validates inputs via Pydantic VerifierInput,
        executes deterministic policy & safety checks against safety guardrails,
        validates output via Pydantic VerifierOutput, and updates the graph state.
        """
        tracer = state.get("tracer")
        start_time = time.time()
        proposal_dict = state.get("proposal", {})
        human_approval_token = state.get("human_approval_token")
        env_context = state.get("env_context", "prod")
        tenant_context = state.get("tenant_context", "default")

        # Pydantic structured input validation. Constructing the model is the
        # validation; it raises on a malformed proposal rather than letting it
        # reach the safety engine.
        VerifierInput(
            proposal=proposal_dict,
            env_context=env_context,
            human_approval_token=human_approval_token,
        )

        # Hydrate ActionProposal domain object
        proposal = ActionProposal(
            action_id=proposal_dict.get("action_id", "ACT-UNKNOWN"),
            tool_name=proposal_dict.get("tool_name", ""),
            service=proposal_dict.get("service", ""),
            environment=proposal_dict.get("environment", env_context),
            tenant=proposal_dict.get("tenant", tenant_context),
            parameters=proposal_dict.get("parameters", {}),
            reasoning=proposal_dict.get("reasoning", ""),
            evidence_citations=proposal_dict.get("evidence_citations", []),
            estimated_blast_radius=proposal_dict.get("estimated_blast_radius", 1),
            autonomy_tier=AutonomyTier(proposal_dict.get("autonomy_tier", 2)),
            rejected_alternatives=proposal_dict.get("rejected_alternatives", [])
        )

        # Execute deterministic safety audit
        safety_result = self.guardrails.evaluate_proposal(
            proposal=proposal,
            env_context=env_context,
            human_approval_token=human_approval_token,
            tenant_context=tenant_context
        )

        decisions = [
            f"Safety check outcome: {'APPROVED' if safety_result.approved else 'REJECTED/BLOCKED'}.",
            f"Determined Autonomy Tier: {safety_result.tier.name}.",
            f"Blast radius analysis verified: {safety_result.blast_radius_analysis.get('total_affected_services', 0)} dependent services."
        ]
        rejected = []
        if not safety_result.approved:
            if safety_result.requires_human_token:
                rejected.append("Rejected autonomous execution: service or blast radius exceeds Tier-2 threshold; held for human signoff.")
            for violation in safety_result.policy_violations:
                rejected.append(f"Rejected action due to violation: {violation}")

        # Narration happens strictly after the verdict, and only when this run
        # produced one. A resumed run re-enters at the Verifier with a decision
        # already in state, and re-narrating it would bill a second generation
        # for an answer that has not changed.
        safety_result_dict = safety_result.to_dict()
        narrative = None
        llm_trace = trace_kwargs(
            "deterministic", None, DETERMINISTIC_TOKENS, DETERMINISTIC_COST_USD
        )
        mode = "deterministic"
        if not state.get("safety_result"):
            narrative, llm_trace, mode = self._narrate(proposal_dict, safety_result_dict)
        if narrative:
            safety_result_dict["risk_narrative"] = narrative
            decisions.append(f"Risk narrative ({mode}): {narrative}")

        duration_ms = (time.time() - start_time) * 1000.0
        if tracer:
            tracer.record_step(
                step_id="STEP-VERIFIER-01",
                workflow_state="SAFETY_VERIFICATION",
                agent=self.role.value,
                action="AUDIT_PROPOSAL_SAFETY_AND_BLAST_RADIUS",
                inputs={"proposal_id": proposal.action_id, "tool": proposal.tool_name, "env_context": env_context},
                outputs=safety_result_dict,
                latency_ms=duration_ms,
                decisions=decisions,
                rejected_alternatives=rejected,
                **llm_trace
            )

        # Pydantic Structured Output Validation
        validated_output = VerifierOutput(
            proposal=proposal.to_dict(),
            safety_result=safety_result_dict,
            verifier_version=self.version
        )

        return {
            "safety_result": validated_output.safety_result,
            "current_state": "SAFETY_VERIFICATION"
        }

    def handle_message(self, message: A2AMessage, context: Dict[str, Any]) -> A2AMessage:
        node_res = self.run_node({
            "proposal": message.payload.get("proposal", {}),
            "human_approval_token": context.get("human_approval_token"),
            "env_context": context.get("env_context", "prod"),
            "tenant_context": context.get("tenant_context", "default"),
            "tracer": context.get("tracer")
        })
        safety_result_dict = node_res["safety_result"]
        proposal_dict = message.payload.get("proposal", {})
        return self.create_message(
            recipient=AgentRole.ORCHESTRATOR,
            message_type=MessageType.SAFETY_DECISION,
            correlation_id=message.correlation_id,
            payload={
                "proposal": proposal_dict,
                "safety_result": safety_result_dict,
                "verifier_version": self.version
            }
        )

