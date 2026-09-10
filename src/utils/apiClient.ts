/**
 * Thin client for the real Mini Agentic AI Platform HTTP API.
 *
 * Every call here hits `mini_platform/api/server.py` directly -- nothing in
 * this module invents or shapes a result. A network failure or a non-2xx
 * response throws `ApiError` rather than returning a plausible-looking
 * fallback, so a UI that stops rendering live data is visibly broken instead
 * of quietly reverting to fixtures.
 */

const API_BASE_URL: string =
  (import.meta as any).env?.VITE_API_BASE_URL || "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    });
  } catch (err) {
    throw new ApiError(
      `Cannot reach the API at ${API_BASE_URL}. Is \`uvicorn mini_platform.api.server:app\` running?`,
      0,
      err
    );
  }

  const text = await res.text();
  const body = text ? JSON.parse(text) : null;

  if (!res.ok) {
    const detail = body?.detail ?? body;
    const message =
      typeof detail === "string" ? detail : detail?.message || `Request failed (${res.status})`;
    throw new ApiError(message, res.status, detail);
  }
  return body as T;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

// ---------------------------------------------------------------------------
// Operational
// ---------------------------------------------------------------------------
export interface HealthResponse {
  status: string;
  platform_version: string;
  api_version: string;
  persisted_runs: number;
  session_environment: string;
  session_tenant: string;
  simulation_id: string;
}
export const getHealth = () => get<HealthResponse>("/health");

export interface SimulationResetResponse {
  status: string;
  simulation_id: string;
  message: string;
}
/**
 * Starts a fresh simulation context: a clean simulated cluster and a clean
 * Tool Gateway idempotency window, without restarting the backend process.
 * Use this between independent test/demo incidents on the same service --
 * otherwise a service a prior incident healed stays healed, and a mutation
 * identical to one already dispatched keeps returning ALREADY_EXECUTED.
 */
export const resetSimulation = () => post<SimulationResetResponse>("/simulation/reset", {});

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------
export interface McpToolDefinition {
  name: string;
  version: string;
  description: string;
  parameters: Record<string, any>;
  read_only: boolean;
  mutating: boolean;
}
export interface ToolCatalogResponse {
  tool_versions: Record<string, string>;
  tools: McpToolDefinition[];
}
export const listTools = () => get<ToolCatalogResponse>("/tools");

export const invokeReadOnlyTool = (
  toolName: string,
  service: string,
  parameters: Record<string, any> = {}
) => post<Record<string, any>>(`/tools/${encodeURIComponent(toolName)}/invoke`, { service, parameters });

// ---------------------------------------------------------------------------
// Knowledge
// ---------------------------------------------------------------------------
export interface ServiceGraphNode {
  id: string;
  label: string;
  tier: string;
  owner: string;
  environment: string;
}
export interface ServiceGraphEdge {
  source: string;
  target: string;
}
export interface ServicesGraphResponse {
  nodes: ServiceGraphNode[];
  edges: ServiceGraphEdge[];
}
export const listServices = () => get<ServicesGraphResponse>("/services");

export interface BlastRadiusAnalysis {
  target_service: string;
  found: boolean;
  service_tier: string;
  owner: string;
  direct_dependents: string[];
  transitive_dependents: string[];
  total_affected_services: number;
  tier_0_impacted: boolean;
  impacted_tiers: string[];
  risk_rating: string;
}
export const getBlastRadius = (service: string) =>
  get<BlastRadiusAnalysis>(`/services/${encodeURIComponent(service)}/blast-radius`);

export interface KnowledgeSearchHit {
  doc_id: string;
  title: string;
  service: string;
  env: string;
  type: string;
  version: string;
  rrf_score: number;
  bm25_score: number;
  dense_score: number;
  citation_snippet: string;
  full_content: string;
}
export interface KnowledgeSearchResponse {
  query: string;
  count: number;
  results: KnowledgeSearchHit[];
}
export const searchKnowledge = (
  query: string,
  opts: { service?: string; environment?: string; docType?: string; topK?: number } = {}
) =>
  post<KnowledgeSearchResponse>("/knowledge/search", {
    query,
    service: opts.service || null,
    environment: opts.environment || null,
    doc_type: opts.docType || null,
    top_k: opts.topK ?? 4,
  });

// ---------------------------------------------------------------------------
// Safety
// ---------------------------------------------------------------------------
export interface SafetyCheckPerformed {
  policy: string;
  passed: boolean;
  [key: string]: any;
}
export interface SafetyPreviewResponse {
  approved: boolean;
  tier: string;
  requires_human_token: boolean;
  policy_violations: string[];
  blast_radius_analysis: BlastRadiusAnalysis;
  explanation: string;
  checks_performed: SafetyCheckPerformed[];
}
export const previewSafety = (
  toolName: string,
  service: string,
  environment: string,
  parameters: Record<string, any>,
  humanApprovalToken?: string
) =>
  post<SafetyPreviewResponse>("/safety/preview", {
    tool_name: toolName,
    service,
    environment,
    parameters,
    human_approval_token: humanApprovalToken || null,
  });

export const previewRedaction = (data: Record<string, any>) =>
  post<{ redacted: Record<string, any> }>("/safety/redact-preview", { data });

// ---------------------------------------------------------------------------
// Incident lifecycle
// ---------------------------------------------------------------------------
export interface SubmitIncidentPayload {
  title: string;
  description: string;
  service: string;
  severity: "SEV-1" | "SEV-2" | "SEV-3";
  environment?: string;
  incident_id?: string;
  human_approval_token?: string;
}
export const submitIncident = (payload: SubmitIncidentPayload) =>
  post<Record<string, any>>("/incidents", payload);

export const approveIncident = (runId: string, humanApprovalToken: string) =>
  post<Record<string, any>>(`/incidents/${encodeURIComponent(runId)}/approve`, {
    human_approval_token: humanApprovalToken,
  });

export const listIncidents = (limit = 50) =>
  get<{ count: number; runs: Record<string, any>[] }>(`/incidents?limit=${limit}`);

export const getIncident = (runId: string) =>
  get<Record<string, any>>(`/incidents/${encodeURIComponent(runId)}`);

export const replayTrace = (runId: string) =>
  get<{ run_id: string; replay: string }>(`/traces/${encodeURIComponent(runId)}/replay`);

// ---------------------------------------------------------------------------
// Evaluation gate
// ---------------------------------------------------------------------------
export interface EvalScenarioSummary {
  id: string;
  name: string;
  service: string;
  environment: string;
  title: string;
  description: string;
  severity: "SEV-1" | "SEV-2" | "SEV-3";
  expected: Record<string, any>;
}
export const listEvalScenarios = () =>
  get<{ count: number; scenarios: EvalScenarioSummary[] }>("/eval/scenarios");

export interface EvalScenarioResult {
  scenario_id: string;
  name: string;
  passed: boolean;
  final_state: string;
  details: string[];
  run_id: string;
}
export interface EvalRunResponse {
  total: number;
  passed: number;
  failed: number;
  pass_rate_pct: number;
  results: EvalScenarioResult[];
}
export const runEvaluationSuite = () => post<EvalRunResponse>("/eval/run", {});
