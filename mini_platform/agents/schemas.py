"""
Typed Pydantic schemas for Agent I/O contracts in LangGraph nodes.
Ensures rigorous schema validation for inputs and outputs between agents.
"""
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


# --- Planner Agent Schemas ---

class DelegatedTask(BaseModel):
    task_id: str
    assigned_to: str
    action: str
    service: Optional[str] = None
    timeframe: Optional[str] = None
    query: Optional[str] = None
    service_filter: Optional[str] = None
    purpose: str


class PlannerInput(BaseModel):
    id: str
    title: str
    description: str
    service: str
    environment: str = "prod"
    severity: str = "SEV-1"
    reported_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PlannerOutput(BaseModel):
    incident: Dict[str, Any]
    symptoms: List[str]
    delegated_tasks: List[DelegatedTask]
    plan_version: str = "1.2.0"


# --- Investigator Agent Schemas ---

class InvestigatorInput(BaseModel):
    incident: Dict[str, Any]
    symptoms: List[str] = Field(default_factory=list)
    delegated_tasks: List[Dict[str, Any]] = Field(default_factory=list)


class InvestigatorEvidence(BaseModel):
    service: str
    environment: str
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    topology: Dict[str, Any] = Field(default_factory=dict)
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    identified_root_cause: Optional[str] = None
    diagnosis_summary: Optional[str] = None
    # Structured record of any tool call the Tool Gateway refused, retained so
    # the audit trace explains gaps in the evidence rather than hiding them.
    tool_errors: List[Dict[str, Any]] = Field(default_factory=list)


class InvestigatorOutput(BaseModel):
    incident: Dict[str, Any]
    evidence: InvestigatorEvidence
    investigator_version: str = "1.4.0"


# --- Ops Agent Schemas ---

class OpsInput(BaseModel):
    incident: Dict[str, Any]
    evidence: Dict[str, Any]


class ActionAlternative(BaseModel):
    alternative: str
    reason_rejected: str


class ActionProposalSchema(BaseModel):
    action_id: str
    tool_name: str
    service: str
    environment: str
    parameters: Dict[str, Any]
    reasoning: str
    evidence_citations: List[Dict[str, Any]] = Field(default_factory=list)
    estimated_blast_radius: int = 1
    autonomy_tier: int = 2
    rejected_alternatives: List[ActionAlternative] = Field(default_factory=list)


class OpsOutput(BaseModel):
    proposal: Dict[str, Any]
    ops_version: str = "1.2.0"


# --- Verifier Agent Schemas ---

class VerifierInput(BaseModel):
    proposal: Dict[str, Any]
    env_context: str = "prod"
    human_approval_token: Optional[str] = None


class SafetyCheckResultSchema(BaseModel):
    approved: bool
    tier: int
    requires_human_token: bool
    policy_violations: List[str] = Field(default_factory=list)
    blast_radius_analysis: Dict[str, Any] = Field(default_factory=dict)
    explanation: str
    checks_performed: List[Dict[str, Any]] = Field(default_factory=list)


class VerifierOutput(BaseModel):
    proposal: Dict[str, Any]
    safety_result: Dict[str, Any]
    verifier_version: str = "1.4.0"
