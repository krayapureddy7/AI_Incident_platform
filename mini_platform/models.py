"""
Core domain models, schemas, and structured A2A message types.
"""
from dataclasses import dataclass, field, asdict
from enum import Enum
import uuid
import datetime
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field


class AgentRole(str, Enum):
    ORCHESTRATOR = "orchestrator"
    PLANNER = "planner"
    INVESTIGATOR = "investigator"
    OPS = "ops"
    VERIFIER = "verifier"


class WorkflowState(str, Enum):
    IDLE = "IDLE"
    TRIAGE = "TRIAGE"
    PLANNING = "PLANNING"
    INVESTIGATING = "INVESTIGATING"
    PROPOSING_ACTION = "PROPOSING_ACTION"
    SAFETY_VERIFICATION = "SAFETY_VERIFICATION"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING_RECOVERY = "VERIFYING_RECOVERY"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MessageType(str, Enum):
    TASK_DELEGATION = "TASK_DELEGATION"
    EVIDENCE_QUERY = "EVIDENCE_QUERY"
    EVIDENCE_REPORT = "EVIDENCE_REPORT"
    ACTION_PROPOSAL = "ACTION_PROPOSAL"
    SAFETY_EVALUATION = "SAFETY_EVALUATION"
    SAFETY_DECISION = "SAFETY_DECISION"
    EXECUTION_REQUEST = "EXECUTION_REQUEST"
    EXECUTION_RESULT = "EXECUTION_RESULT"
    STATE_TRANSITION = "STATE_TRANSITION"


class AutonomyTier(int, Enum):
    TIER_1_READONLY = 1          # Read-only observation, telemetry querying
    TIER_2_VERIFIED_AUTO = 2     # Low blast-radius idempotent remediation (auto-executed if verified)
    TIER_3_HUMAN_APPROVAL = 3    # High blast-radius or tier-0 service mutations requiring human approval


class A2AMessage(BaseModel):
    """Structured Agent-to-Agent message envelope enforced via Pydantic."""
    sender: AgentRole
    recipient: AgentRole
    message_type: MessageType
    correlation_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        data = self.model_dump()
        data["sender"] = self.sender.value if hasattr(self.sender, "value") else str(self.sender)
        data["recipient"] = self.recipient.value if hasattr(self.recipient, "value") else str(self.recipient)
        data["message_type"] = self.message_type.value if hasattr(self.message_type, "value") else str(self.message_type)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "A2AMessage":
        return cls.model_validate(data)


@dataclass
class Incident:
    """Incident declaration."""
    id: str
    title: str
    description: str
    service: str
    environment: str = "prod"
    tenant: str = "default"
    severity: str = "SEV-1"
    reported_at: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ActionProposal:
    """Concrete remediation action proposed by Infra/Ops Agent."""
    action_id: str
    tool_name: str
    service: str
    environment: str
    parameters: Dict[str, Any]
    reasoning: str
    evidence_citations: List[Dict[str, Any]]
    estimated_blast_radius: int
    autonomy_tier: AutonomyTier
    tenant: str = "default"
    rejected_alternatives: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["autonomy_tier"] = self.autonomy_tier.value
        return d


@dataclass
class SafetyCheckResult:
    """Result from Verifier/Safety Agent."""
    approved: bool
    tier: AutonomyTier
    requires_human_token: bool
    policy_violations: List[str]
    blast_radius_analysis: Dict[str, Any]
    explanation: str
    checks_performed: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["tier"] = self.tier.value
        return d


@dataclass
class TraceStep:
    """Auditable trace step."""
    step_id: str
    workflow_state: str
    agent: str
    action: str
    inputs_redacted: Dict[str, Any]
    outputs_redacted: Dict[str, Any]
    latency_ms: float
    estimated_tokens: int
    estimated_cost_usd: float
    decisions: List[str]
    rejected_alternatives: List[str]
    # Reasoning provenance. `mode` records how the step reached its conclusion:
    # "deterministic" (no model configured), "llm" (a validated generation), or
    # "fallback:<reason>" (a generation was attempted and rejected). Together
    # with `model` and `prompt_version` this makes a decision attributable to an
    # exact artifact, which is what the rollback procedure depends on.
    #
    # Usage counts are named to survive redaction: `redact_sensitive_data`
    # blanks any key containing "token", so `usage_in`/`usage_out` rather than
    # the vendors' prompt_tokens/completion_tokens.
    mode: str = "deterministic"
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    validation_result: Optional[str] = None
    usage_in: int = 0
    usage_out: int = 0
    llm_latency_ms: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
