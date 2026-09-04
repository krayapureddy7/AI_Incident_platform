export type AutonomyTier = 1 | 2 | 3;

export type WorkflowState =
  | "IDLE"
  | "TRIAGE"
  | "PLANNING"
  | "INVESTIGATING"
  | "PROPOSING"
  | "SAFETY_VERIFY"
  | "EXECUTING"
  | "VERIFY_RECOVERY"
  | "COMPLETED"
  | "AWAITING_APPROVAL"
  | "FAILED";

export type AgentRole =
  | "orchestrator"
  | "planner"
  | "investigator"
  | "ops"
  | "verifier";

export type MessageType =
  | "TASK_DELEGATION"
  | "EVIDENCE_REPORT"
  | "ACTION_PROPOSAL"
  | "SAFETY_DECISION"
  | "EXECUTION_DISPATCH"
  | "ERROR_ENVELOPE";

export interface Incident {
  id: string;
  title: string;
  description: string;
  service: string;
  environment: string;
  severity: "SEV-1" | "SEV-2" | "SEV-3";
}

export interface A2AMessage {
  message_id: string;
  correlation_id: string;
  sender: AgentRole;
  recipient: AgentRole;
  message_type: MessageType;
  timestamp: string;
  payload: Record<string, any>;
}

export interface ActionProposal {
  action_id: string;
  tool_name: string;
  service: string;
  environment: string;
  parameters: Record<string, any>;
  reasoning: string;
  evidence_citations: Array<{
    doc_id: string;
    title: string;
    score: number;
    snippet: string;
  }>;
  rejected_alternatives: Array<{
    tool_name: string;
    parameters: Record<string, any>;
    rejection_reason: string;
  }>;
  estimated_blast_radius: number;
  autonomy_tier: AutonomyTier;
}

export interface SafetyDecision {
  action_id: string;
  approved: boolean;
  tier: AutonomyTier;
  requires_human_token: boolean;
  policy_violations: string[];
  blast_radius_info: {
    target_service: string;
    service_tier: string;
    direct_dependents: string[];
    transitive_dependents: string[];
    total_affected_services: number;
    risk_rating: string;
  };
  audit_rationale: string;
}

export interface ExecutionStep {
  step_number: number;
  timestamp: string;
  agent: string;
  action: string;
  state_transition: {
    from: WorkflowState;
    to: WorkflowState;
  };
  latency_ms: number;
  tokens_used: number;
  decisions: string[];
  message?: A2AMessage;
  details?: Record<string, any>;
}

export interface SimulationResult {
  run_id: string;
  incident: Incident;
  final_state: WorkflowState;
  status: "SUCCESS" | "AWAITING_APPROVAL" | "FAILED";
  proposal?: ActionProposal;
  safety_decision?: SafetyDecision;
  execution_result?: {
    ok: boolean;
    status: string;
    message: string;
  };
  telemetry_before: {
    status: string;
    memory_utilization_pct: number;
    cpu_utilization_pct: number;
    p99_latency_ms: number;
    error_rate_pct: number;
    replicas: number;
  };
  telemetry_after?: {
    status: string;
    memory_utilization_pct: number;
    cpu_utilization_pct: number;
    p99_latency_ms: number;
    error_rate_pct: number;
    replicas: number;
  };
  steps: ExecutionStep[];
  total_tokens: number;
  total_latency_ms: number;
  total_cost_usd: number;
}

export interface BenchmarkScenario {
  id: string;
  name: string;
  incident: Incident;
  env_context?: string;
  expected_trajectory: {
    root_cause?: string;
    proposed_tool?: string;
    expected_autonomy_tier?: number;
    expected_final_state: WorkflowState;
    expected_post_status?: string;
    is_tier_0?: boolean;
    must_require_human_token?: boolean;
    expected_rejection_code?: string;
  };
}
