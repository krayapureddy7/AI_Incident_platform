"""
Typed Pydantic schemas for Agent I/O contracts in LangGraph nodes.
Ensures rigorous schema validation for inputs and outputs between agents.
"""
from typing import Dict, Any, List, Literal, Optional
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
    # The Verifier compares this against the session's tenant authority, and the
    # Tool Gateway rejects a mismatch, so it cannot be omitted from a validated
    # proposal.
    tenant: str = "default"
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


# --- LLM reasoning contracts -------------------------------------------------
#
# These are the schemas a language model must satisfy before any of its output
# reaches graph state. They are deliberately narrower than the agent I/O schemas
# above: where an agent may emit any string, the model is constrained to a closed
# vocabulary, because downstream code branches on these exact values.

#: Symptom vocabulary the Planner may emit. The Investigator builds its retrieval
#: query from this list, so an invented symptom would silently change which
#: runbooks are retrieved.
SYMPTOM_LITERALS = (
    "MEMORY_EXHAUSTION_OR_LEAK",
    "LATENCY_SPIKE_OR_TIMEOUT",
    "HIGH_ERROR_RATE",
    "RESOURCE_OR_CONNECTION_STARVATION",
    "UNKNOWN_DEGRADATION",
)

#: Root-cause vocabulary the Investigator may emit. The Ops agent dispatches on
#: these exact strings; an unrecognised value would fall through to the default
#: branch and silently change the proposed remediation, so the type system
#: rejects it instead.
ROOT_CAUSE_LITERALS = (
    "MEMORY_LEAK_HEAP_EXHAUSTION",
    "CPU_AND_CONNECTION_SATURATION",
    "SERVICE_UNRESPONSIVE",
    "EVIDENCE_UNAVAILABLE",
)

SymptomLiteral = Literal[
    "MEMORY_EXHAUSTION_OR_LEAK",
    "LATENCY_SPIKE_OR_TIMEOUT",
    "HIGH_ERROR_RATE",
    "RESOURCE_OR_CONNECTION_STARVATION",
    "UNKNOWN_DEGRADATION",
]

RootCauseLiteral = Literal[
    "MEMORY_LEAK_HEAP_EXHAUSTION",
    "CPU_AND_CONNECTION_SATURATION",
    "SERVICE_UNRESPONSIVE",
    "EVIDENCE_UNAVAILABLE",
]


class LLMPlannerReasoning(BaseModel):
    """Planner generation: incident triage into a closed symptom vocabulary."""

    symptoms: List[SymptomLiteral] = Field(..., min_length=1)
    triage_summary: str = Field(..., min_length=1)


class LLMDiagnosis(BaseModel):
    """
    Investigator generation: root-cause analysis over already-retrieved evidence.

    ``cited_doc_ids`` is checked against the documents actually returned by the
    Hybrid RAG search. A model that cites a document it was not shown has
    hallucinated, and the diagnosis is rejected.
    """

    identified_root_cause: RootCauseLiteral
    diagnosis_summary: str = Field(..., min_length=1)
    cited_doc_ids: List[str] = Field(default_factory=list)


class LLMRemediationProposal(BaseModel):
    """
    Ops generation: the remediation to propose.

    ``tool_name`` and ``service`` are validated against the published tool
    catalog and the knowledge graph before the proposal is accepted -- the schema
    only guarantees shape, not that the named tool or service exists.
    """

    tool_name: str = Field(..., min_length=1)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    reasoning: str = Field(..., min_length=1)
    rejected_alternatives: List[ActionAlternative] = Field(default_factory=list)


class LLMRiskNarrative(BaseModel):
    """
    Verifier generation: a plain-language reading of a verdict already reached.

    Advisory only. It is produced after the deterministic engine has decided and
    is never read back into the decision, so it cannot influence the outcome.
    """

    risk_narrative: str = Field(..., min_length=1)
