"""
Deterministic Safety Engine & Guardrails.
Enforces Autonomy Tiers, Blast Radius Boundaries, Invariant Policies, and Environment Isolation.
"""

from typing import Dict, Any, List, Optional, Tuple
from app.tools.contracts import (
    MAX_REPLICA_CEILING,
    READ_ONLY_TOOLS,
    TOOL_VERSIONS,
)
from ..models import ActionProposal, SafetyCheckResult, AutonomyTier
from ..knowledge.knowledge_graph import GLOBAL_KNOWLEDGE_GRAPH, KnowledgeGraph
from .approval import verify_approval_token

#: Version of the deterministic policy matrix and tier rules.
__version__ = "1.5.0"

#: Replica count at or below which a scale is eligible for unattended execution.
UNATTENDED_SCALE_LIMIT = 6


def _validated_replicas(params: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    """
    Coerce and bounds-check a requested replica count.

    Returns ``(value, violation)`` with exactly one side populated. Parameters
    arrive from agent output and untrusted callers, so a malformed value has to
    become a policy rejection here -- if it escaped as a ``TypeError`` the
    engine would fail open with a crash instead of a denial.
    """
    raw = params.get("replicas", 0)

    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, (
            f"INVARIANT_VIOLATION: Replica count must be an integer, got {type(raw).__name__}."
        )
    if raw <= 0:
        return None, (
            "INVARIANT_VIOLATION: Cannot scale service replicas to <= 0 during active remediation."
        )
    if raw > MAX_REPLICA_CEILING:
        return None, (
            f"SCALE_CEILING_EXCEEDED: Requested replicas ({raw}) exceeds "
            f"safety cap of {MAX_REPLICA_CEILING}."
        )
    return raw, None


class SafetyGuardrails:
    """
    Deterministic runtime policy engine.
    Evaluates every ActionProposal before execution.
    """

    def __init__(self, kg: KnowledgeGraph = None):
        self.kg = kg or GLOBAL_KNOWLEDGE_GRAPH

    def evaluate_proposal(
        self,
        proposal: ActionProposal,
        env_context: str = "prod",
        human_approval_token: str = None,
        tenant_context: str = "default"
    ) -> SafetyCheckResult:
        """
        Evaluate proposal against formal safety policies and determine autonomy tier & approval.
        """
        violations: List[str] = []
        checks_performed: List[Dict[str, Any]] = []

        # Check 1: Tool presence and parameter bounds
        tool = proposal.tool_name
        params = proposal.parameters if isinstance(proposal.parameters, dict) else {}
        svc = proposal.service

        tool_known = tool in TOOL_VERSIONS
        checks_performed.append({
            "policy": "TOOL_REGISTRATION",
            "passed": tool_known,
            "details": f"Tool '{tool}' is {'registered' if tool_known else 'not in the published catalog'}."
        })
        if not tool_known:
            violations.append(
                f"UNKNOWN_TOOL: '{tool}' is not a registered tool and cannot be dispatched."
            )

        # Replica bounds are validated once, here, and the validated value is
        # the only one the tier rules below are allowed to read.
        replicas: Optional[int] = None
        if tool == "simulate_scale":
            replicas, replica_violation = _validated_replicas(params)
            if replica_violation:
                violations.append(replica_violation)

        # Check 2: Environment Barrier
        env_match = (proposal.environment == env_context)
        checks_performed.append({
            "policy": "ENVIRONMENT_ISOLATION",
            "passed": env_match,
            "details": f"Proposal env '{proposal.environment}' matches session env '{env_context}'"
        })
        if not env_match:
            violations.append(
                f"CROSS_ENVIRONMENT_VIOLATION: Attempted to run action targeted at '{proposal.environment}' within '{env_context}' context."
            )

        # Check 2b: Tenant Barrier
        prop_tenant = getattr(proposal, "tenant", "default") or "default"
        tenant_match = (prop_tenant == tenant_context)
        checks_performed.append({
            "policy": "TENANT_ISOLATION",
            "passed": tenant_match,
            "details": f"Proposal tenant '{prop_tenant}' matches session tenant '{tenant_context}'"
        })
        if not tenant_match:
            violations.append(
                f"CROSS_TENANT_VIOLATION: Attempted to run action targeted at tenant '{prop_tenant}' within '{tenant_context}' context."
            )

        # Check 3: Blast Radius Traversal
        blast_info = self.kg.calculate_blast_radius(svc)
        total_affected = blast_info["total_affected_services"]
        tier_0_impacted = blast_info["tier_0_impacted"]
        svc_tier = blast_info.get("service_tier", "tier-2")

        checks_performed.append({
            "policy": "BLAST_RADIUS_ASSESSMENT",
            "passed": True,
            "details": (
                f"{total_affected} affected service(s), risk {blast_info['risk_rating']}, "
                f"tier-0 impacted: {tier_0_impacted}"
            ),
            "total_affected_services": total_affected,
            "direct_dependents": blast_info["direct_dependents"],
            "transitive_dependents": blast_info["transitive_dependents"],
            "tier_0_impacted": tier_0_impacted,
            "risk_rating": blast_info["risk_rating"]
        })

        # Check 4: Specific Tool Risk Invariants
        # Validated in Check 1, so that the tier rules below and the violation
        # list can never disagree about what the parameters mean.

        # Check 5: Autonomy Tier Classification
        # Rule: Read-only queries -> Tier 1
        # Rule: Tier-0 services or tier-0 impacted -> Tier 3 (Human Approval mandatory)
        # Rule: High blast radius (>2 direct dependents or >4 total transitive) -> Tier 3
        # Rule: Low-risk mutations on tier-1/tier-2 with direct dependents <= 2 -> Tier 2 (Verified Auto)
        direct_dep_count = len(blast_info["direct_dependents"])
        required_tier = AutonomyTier.TIER_2_VERIFIED_AUTO

        if tool in READ_ONLY_TOOLS:
            required_tier = AutonomyTier.TIER_1_READONLY
        elif svc_tier == "tier-0" or tier_0_impacted or direct_dep_count > 2 or total_affected > 4:
            required_tier = AutonomyTier.TIER_3_HUMAN_APPROVAL
        elif tool == "simulate_restart" and svc_tier != "tier-0" and direct_dep_count <= 2:
            required_tier = AutonomyTier.TIER_2_VERIFIED_AUTO
        elif (
            tool == "simulate_scale"
            and replicas is not None
            and replicas <= UNATTENDED_SCALE_LIMIT
            and direct_dep_count <= 2
        ):
            required_tier = AutonomyTier.TIER_2_VERIFIED_AUTO
        else:
            required_tier = AutonomyTier.TIER_3_HUMAN_APPROVAL

        # Check 6: Human Approval Token requirement
        # Invariant violations are hard policy failures, not waiting for human token
        has_violations = (len(violations) > 0)
        requires_human = (required_tier == AutonomyTier.TIER_3_HUMAN_APPROVAL) and not has_violations

        # The token is authenticated against this exact proposal. A signature
        # over the action id, tool, service, environment, tenant, and parameter
        # hash means an approval cannot be forged from the public token format,
        # nor carried over from a different action.
        approval = verify_approval_token(human_approval_token, proposal)
        has_valid_human_token = approval.valid

        if requires_human and not has_valid_human_token:
            checks_performed.append({
                "policy": "TIER_3_HUMAN_GOVERNANCE",
                "passed": False,
                "reason": f"Service '{svc}' is {svc_tier} with blast radius {total_affected} dependents. Requires explicit human confirmation.",
                "approval_status": approval.reason
            })
        else:
            checks_performed.append({
                "policy": "TIER_GOVERNANCE",
                "passed": True,
                "tier": required_tier.name,
                "approver": approval.approver if has_valid_human_token else None
            })

        # A presented-but-rejected token is an audit event in its own right: it
        # separates "nobody approved this" from "someone tried to approve it
        # with a token that did not hold up".
        if human_approval_token and not has_valid_human_token:
            checks_performed.append({
                "policy": "APPROVAL_TOKEN_AUTHENTICITY",
                "passed": False,
                "reason": approval.reason
            })

        # Final approval determination
        is_approved = (len(violations) == 0) and (not requires_human or has_valid_human_token)

        if not is_approved:
            if violations:
                explanation = f"REJECTED by safety engine due to policy violations: {'; '.join(violations)}"
            else:
                explanation = (
                    f"BLOCKED FOR HUMAN APPROVAL: Action classified as {required_tier.name} "
                    f"(Service: {svc}, Tier: {svc_tier}, Blast Radius: {total_affected} dependents)."
                )
                if human_approval_token:
                    explanation += f" Supplied approval token rejected: {approval.reason}"
        else:
            explanation = f"APPROVED by safety engine under {required_tier.name}. Blast radius verified ({total_affected} dependents, tier: {svc_tier})."

        return SafetyCheckResult(
            approved=is_approved,
            tier=required_tier,
            requires_human_token=requires_human and not has_valid_human_token,
            policy_violations=violations,
            blast_radius_analysis=blast_info,
            explanation=explanation,
            checks_performed=checks_performed
        )


GLOBAL_SAFETY_GUARDRAILS = SafetyGuardrails()
