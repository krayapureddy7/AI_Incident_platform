"""
Security Tool Gateway.

Acts as the mandatory, single point-of-entry security boundary for all tool
executions -- read-only observation included. Enforces the agent permission
matrix, Pydantic schema validation, deterministic policy evaluation,
tenant/environment isolation, blast-radius checks, autonomy gating, and
idempotency.

Architectural invariant
-----------------------
No agent holds a reference to a tool implementation, a FastMCP server, or a
cloud SDK. Every tool call -- including `get_logs`/`get_metrics` -- is routed
through `ToolGateway`, so a rejected call can never reach the tool layer.
`fastmcp_call_count` is incremented immediately before dispatch and is asserted
in the contract tests to prove this.
"""
from typing import Any, Dict, Optional
import hashlib
import json
import time

from pydantic import BaseModel, Field, ValidationError

from mini_platform.models import ActionProposal, AutonomyTier, SafetyCheckResult
from mini_platform.safety.guardrails import SafetyGuardrails

from .client import GLOBAL_TOOL_CLIENT, ToolClient
from .contracts import (
    READ_ONLY_TOOLS,
    TOOL_VERSIONS,
    is_mutating,
    permitted_tools,
    tool_version,
)

#: Version of the authorization pipeline. Bump on any change to the checks,
#: their order, or the error codes they emit.
__version__ = "2.0.0"

__all__ = [
    "__version__",
    "ToolGateway",
    "GLOBAL_TOOL_GATEWAY",
    "ProposalValidationSchema",
    "GatewayExecutionError",
]


class ProposalValidationSchema(BaseModel):
    """Pydantic schema enforcing structured action proposal contracts."""

    tool_name: str = Field(..., min_length=1, description="Target tool name to execute")
    service: str = Field(..., min_length=1, description="Target service identifier")
    environment: str = Field(default="prod", min_length=1, description="Execution environment scope")
    tenant: str = Field(default="default", min_length=1, description="Tenant organization scope")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Tool-specific argument payload")
    reasoning: Optional[str] = Field(default="", description="Operator or agent justification")
    caller_role: str = Field(default="ops", description="Agent role initiating proposal")
    human_approval_token: Optional[str] = Field(
        default=None, description="Cryptographic human token for Tier-3"
    )


class GatewayExecutionError(Exception):
    """Exception raised for hard policy violations in the Tool Gateway."""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ToolGateway:
    """
    Authorized execution gateway.

    Agents MUST NOT bypass this gateway to reach tool implementations. The
    gateway owns a `ToolClient`, which is the only component permitted to speak
    the FastMCP protocol.
    """

    def __init__(
        self,
        client: Optional[ToolClient] = None,
        guardrails: Optional[SafetyGuardrails] = None,
    ):
        self.client = client or GLOBAL_TOOL_CLIENT
        self.guardrails = guardrails or SafetyGuardrails()
        self.idempotency_store: Dict[str, Dict[str, Any]] = {}
        # Dispatch counters, asserted by the contract tests and the evaluation
        # gate to prove the tool layer is never reached on a rejected action.
        # `mutating_call_count` is the safety-critical one: a rejected or held
        # action must leave it unchanged. `fastmcp_call_count` counts all
        # dispatches, reads included, since reads also traverse this gateway.
        self.fastmcp_call_count: int = 0
        self.mutating_call_count: int = 0

    # -- Helpers ------------------------------------------------------------
    @staticmethod
    def _error(
        tool_name: str,
        code: str,
        message: str,
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build a structured error envelope consistent with the tool contract."""
        error: Dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
        if details:
            error["details"] = details

        metadata: Dict[str, Any] = {
            "tool": tool_name,
            "version": tool_version(tool_name),
            "fastmcp_invoked": False,
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        return {"ok": False, "error": error, "metadata": metadata}

    def _compute_idempotency_key(
        self, service: str, tool_name: str, params: Dict[str, Any], env: str, tenant: str
    ) -> str:
        """Derive deterministic hash for idempotency deduplication."""
        raw_repr = json.dumps(
            {"service": service, "tool": tool_name, "params": params, "env": env, "tenant": tenant},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw_repr.encode("utf-8")).hexdigest()

    # -- Primary entry point ------------------------------------------------
    def execute_proposal(
        self,
        proposal: Dict[str, Any],
        env_context: str = "prod",
        tenant_context: str = "default",
        caller_role: str = "orchestrator",
        human_approval_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Full-lifecycle gateway execution following the mandatory flow:

        Proposal -> Pydantic validation -> permission matrix -> tenant check ->
        environment check -> policy/blast-radius/autonomy evaluation ->
        human approval gate -> idempotency -> ToolClient -> FastMCP server.

        Returns a structured envelope. Rejections never reach the tool layer.
        """
        tool_name = proposal.get("tool_name", "")

        # 1. Tool registration check.
        if tool_name not in TOOL_VERSIONS:
            return self._error(
                tool_name,
                "TOOL_NOT_FOUND",
                f"Tool '{tool_name}' is not registered on this tool server.",
                details={"available_tools": sorted(TOOL_VERSIONS)},
            )

        # 2. Agent permission matrix (deny-by-default for unknown roles).
        allowed = permitted_tools(caller_role)
        if tool_name not in allowed:
            if not allowed:
                message = (
                    f"Agent role '{caller_role}' has no tool execution privileges. "
                    "Execution blocked at gateway."
                )
                code = "AUTONOMY_LIMIT"
            else:
                message = (
                    f"Agent role '{caller_role}' is not permitted to invoke '{tool_name}'. "
                    f"Permitted tools: {sorted(allowed)}."
                )
                code = "POLICY_DENIED"
            return self._error(
                tool_name,
                code,
                message,
                details={"caller_role": caller_role, "permitted_tools": sorted(allowed)},
            )

        # 3. Pydantic schema validation.
        try:
            validated = ProposalValidationSchema(
                tool_name=tool_name,
                service=proposal.get("service", ""),
                environment=proposal.get("environment", env_context),
                tenant=proposal.get("tenant", tenant_context),
                parameters=proposal.get("parameters", {}),
                reasoning=proposal.get("reasoning", ""),
                caller_role=caller_role,
                human_approval_token=human_approval_token,
            )
        except ValidationError as ve:
            return self._error(
                tool_name,
                "INVALID_ARGUMENT",
                f"Action proposal failed Pydantic contract validation: {ve}",
                details={"errors": ve.errors()},
            )

        # 4. Tenant boundary check.
        if validated.tenant.strip().lower() != tenant_context.strip().lower():
            return self._error(
                tool_name,
                "TENANT_MISMATCH",
                f"Proposal tenant '{validated.tenant}' does not match session tenant "
                f"context '{tenant_context}'.",
                details={"proposal_tenant": validated.tenant, "session_tenant": tenant_context},
            )

        # 5. Environment boundary check.
        if validated.environment.strip().lower() != env_context.strip().lower():
            return self._error(
                tool_name,
                "ENVIRONMENT_MISMATCH",
                f"Requested environment '{validated.environment}' does not match execution "
                f"context '{env_context}'. Cross-environment execution is prohibited.",
                details={
                    "proposal_environment": validated.environment,
                    "session_environment": env_context,
                },
            )

        # 6. Deterministic policy, blast radius, and autonomy evaluation.
        action_prop = ActionProposal(
            action_id=proposal.get("action_id") or f"ACT-{int(time.time() * 1000)}",
            tool_name=validated.tool_name,
            service=validated.service,
            environment=validated.environment,
            tenant=validated.tenant,
            parameters=validated.parameters,
            reasoning=validated.reasoning or "",
            evidence_citations=[],
            estimated_blast_radius=proposal.get("estimated_blast_radius", 0),
            autonomy_tier=AutonomyTier.TIER_2_VERIFIED_AUTO,
        )

        safety_eval: SafetyCheckResult = self.guardrails.evaluate_proposal(
            proposal=action_prop,
            env_context=env_context,
            human_approval_token=human_approval_token,
            tenant_context=tenant_context,
        )

        if not safety_eval.approved:
            # Rejected or blocked -- the tool layer is NEVER reached.
            if safety_eval.requires_human_token:
                err_code = "AUTONOMY_LIMIT"
                err_msg = (
                    "Tier-3 mutation requires human cryptographic authorization: "
                    f"{safety_eval.explanation}"
                )
            else:
                has_blast = any("BLAST" in v for v in safety_eval.policy_violations)
                err_code = "BLAST_RADIUS_EXCEEDED" if has_blast else "POLICY_DENIED"
                err_msg = safety_eval.explanation

            return self._error(
                tool_name,
                err_code,
                err_msg,
                details={
                    "policy_violations": safety_eval.policy_violations,
                    "autonomy_tier": safety_eval.tier.value,
                    "requires_human_token": safety_eval.requires_human_token,
                },
            )

        # 7. Idempotency check (mutating tools only).
        mutating = is_mutating(validated.tool_name)
        idempotency_key = self._compute_idempotency_key(
            service=validated.service,
            tool_name=validated.tool_name,
            params=validated.parameters,
            env=validated.environment,
            tenant=validated.tenant,
        )

        if mutating and idempotency_key in self.idempotency_store:
            prior = self.idempotency_store[idempotency_key]
            envelope = self._error(
                tool_name,
                "ALREADY_EXECUTED",
                f"Action '{tool_name}' on '{validated.service}' was already executed within "
                "the idempotency window.",
                details={
                    "original_execution_timestamp": prior.get("timestamp"),
                    "idempotency_key": idempotency_key,
                },
                extra_metadata={"idempotent": True},
            )
            envelope["cached_result"] = prior.get("result")
            return envelope

        # 8. Authorized dispatch to the tool layer via the MCP client.
        self.fastmcp_call_count += 1
        if mutating:
            self.mutating_call_count += 1

        tool_args = dict(validated.parameters)
        tool_args["service"] = validated.service
        tool_args["environment"] = validated.environment
        tool_args["tenant"] = validated.tenant

        execution_resp = self.client.call_tool_sync(name=validated.tool_name, arguments=tool_args)

        if isinstance(execution_resp, dict):
            metadata = execution_resp.setdefault("metadata", {})
            metadata.setdefault("tool", validated.tool_name)
            metadata.setdefault("version", tool_version(validated.tool_name))
            metadata["fastmcp_invoked"] = True
            metadata["autonomy_tier"] = safety_eval.tier.value

        if mutating and execution_resp.get("ok"):
            self.idempotency_store[idempotency_key] = {
                "timestamp": time.time(),
                "result": execution_resp,
                "tool": validated.tool_name,
                "service": validated.service,
            }

        return execution_resp

    # -- Read-only convenience ---------------------------------------------
    def execute_read_only(
        self,
        tool_name: str,
        service: str,
        environment: str = "prod",
        tenant: str = "default",
        caller_role: str = "investigator",
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute an observation tool through the full gateway pipeline.

        This is the only path by which the Investigator Agent reads telemetry.
        A mutating tool name supplied here is rejected before dispatch, both by
        this guard and by the permission matrix in `execute_proposal`.
        """
        if tool_name not in READ_ONLY_TOOLS:
            return self._error(
                tool_name,
                "POLICY_DENIED",
                f"'{tool_name}' is not a read-only tool and cannot be invoked through the "
                "read-only gateway path.",
                details={"read_only_tools": sorted(READ_ONLY_TOOLS)},
            )

        return self.execute_proposal(
            proposal={
                "tool_name": tool_name,
                "service": service,
                "environment": environment,
                "tenant": tenant,
                "parameters": dict(extra_params or {}),
                "reasoning": "Read-only evidence collection.",
            },
            env_context=environment,
            tenant_context=tenant,
            caller_role=caller_role,
        )

    # -- Introspection ------------------------------------------------------
    def reset_idempotency_window(self) -> None:
        """Clear the idempotency cache. Intended for test and evaluation setup."""
        self.idempotency_store.clear()


# Global default gateway bound to the default tool client.
GLOBAL_TOOL_GATEWAY = ToolGateway()
