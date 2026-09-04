import {
  Incident,
  SimulationResult,
  ExecutionStep,
  ActionProposal,
  SafetyDecision,
  A2AMessage,
  AutonomyTier,
} from "../types";
import {
  KNOWLEDGE_DOCUMENTS,
  SERVICES_GRAPH,
  KnowledgeDocument,
} from "../data/platformData";

// Tokenizer & String utils
export function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9_\-\s]/g, " ")
    .split(/\s+/)
    .filter((t) => t.length > 1);
}

// Simple hash for dense semantic projection
function tokenHash(token: string, dim: number = 256): number {
  let hash = 0;
  for (let i = 0; i < token.length; i++) {
    hash = (hash << 5) - hash + token.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash) % dim;
}

// Hybrid RAG Search Implementation
export function searchHybridRAG(
  query: string,
  serviceFilter?: string,
  topK: number = 3
): Array<{
  doc_id: string;
  title: string;
  service: string;
  type: string;
  rrf_score: number;
  bm25_score: number;
  dense_score: number;
  citation_snippet: string;
}> {
  const queryTokens = tokenize(query);
  const filteredDocs = serviceFilter
    ? KNOWLEDGE_DOCUMENTS.filter(
        (d) => d.service === serviceFilter || d.service === "global"
      )
    : KNOWLEDGE_DOCUMENTS;

  if (filteredDocs.length === 0) return [];

  // BM25 Scoring
  const N = filteredDocs.length;
  const k1 = 1.5;
  const b = 0.75;
  const docLens = filteredDocs.map(
    (d) => tokenize(d.title + " " + d.content).length
  );
  const avgDl = docLens.reduce((a, b) => a + b, 0) / N;

  const bm25Scores = filteredDocs.map((doc, idx) => {
    const docTokens = tokenize(doc.title + " " + doc.content);
    const tokenFreq: Record<string, number> = {};
    for (const t of docTokens) tokenFreq[t] = (tokenFreq[t] || 0) + 1;

    let score = 0.0;
    for (const qt of queryTokens) {
      const f = tokenFreq[qt] || 0;
      if (f > 0) {
        // Document frequency in filtered set
        const df = filteredDocs.filter((d) =>
          (d.title + " " + d.content).toLowerCase().includes(qt)
        ).length;
        const idf = Math.log((N - df + 0.5) / (df + 0.5) + 1.0);
        const tfNorm =
          (f * (k1 + 1)) / (f + k1 * (1 - b + (b * docLens[idx]) / avgDl));
        score += Math.max(0, idf) * tfNorm;
      }
    }
    // Boost service match
    if (serviceFilter && doc.service === serviceFilter) {
      score += 1.5;
    }
    return { doc, score };
  });

  // Dense Semantic Scoring (256-dim feature hashing)
  const dim = 256;
  const queryVec = new Array(dim).fill(0);
  for (const t of queryTokens) queryVec[tokenHash(t, dim)] += 1.0;
  const qNorm = Math.sqrt(queryVec.reduce((sum, v) => sum + v * v, 0)) || 1.0;

  const denseScores = filteredDocs.map((doc) => {
    const docTokens = tokenize(doc.title + " " + doc.content);
    const docVec = new Array(dim).fill(0);
    for (const t of docTokens) docVec[tokenHash(t, dim)] += 1.0;
    const dNorm = Math.sqrt(docVec.reduce((sum, v) => sum + v * v, 0)) || 1.0;

    let dot = 0.0;
    for (let i = 0; i < dim; i++) dot += queryVec[i] * docVec[i];
    const cosSim = dot / (qNorm * dNorm);
    return { doc, score: Math.max(0, cosSim) };
  });

  // Sort ranks
  const bm25Sorted = [...bm25Scores].sort((a, b) => b.score - a.score);
  const denseSorted = [...denseScores].sort((a, b) => b.score - a.score);

  const bm25RankMap: Record<string, number> = {};
  bm25Sorted.forEach((item, r) => (bm25RankMap[item.doc.id] = r + 1));

  const denseRankMap: Record<string, number> = {};
  denseSorted.forEach((item, r) => (denseRankMap[item.doc.id] = r + 1));

  // Reciprocal Rank Fusion (k=60)
  const results = filteredDocs.map((doc) => {
    const rBM25 = bm25RankMap[doc.id] || 999;
    const rDense = denseRankMap[doc.id] || 999;
    const rrf = 1.0 / (60 + rBM25) + 1.0 / (60 + rDense);
    const bm25Item = bm25Scores.find((x) => x.doc.id === doc.id);
    const denseItem = denseScores.find((x) => x.doc.id === doc.id);

    return {
      doc_id: doc.id,
      title: doc.title,
      service: doc.service,
      type: doc.type,
      rrf_score: Number(rrf.toFixed(4)),
      bm25_score: Number((bm25Item?.score || 0).toFixed(3)),
      dense_score: Number((denseItem?.score || 0).toFixed(3)),
      citation_snippet: doc.content.substring(0, 160) + "...",
    };
  });

  results.sort((a, b) => b.rrf_score - a.rrf_score);
  return results.slice(0, topK);
}

// Knowledge Graph Traversal & Blast Radius BFS
export function calculateBlastRadius(
  serviceName: string,
  maxDepth: number = 3
) {
  const node = SERVICES_GRAPH[serviceName];
  if (!node) {
    return {
      target_service: serviceName,
      found: false,
      direct_dependents: [],
      transitive_dependents: [],
      total_affected_services: 0,
      tier_0_impacted: false,
      risk_rating: "UNKNOWN",
      service_tier: "unknown",
      owner: "unknown",
    };
  }

  const directDependents = [...node.dependents];
  const visited = new Set<string>([serviceName]);
  const transitiveDependents: string[] = [];
  let tier0Impacted = node.tier === "tier-0";

  // Queue of [service, depth]
  const queue: Array<[string, number]> = directDependents.map((dep) => [dep, 1]);

  while (queue.length > 0) {
    const [curr, depth] = queue.shift()!;
    if (visited.has(curr)) continue;
    visited.add(curr);

    const currNode = SERVICES_GRAPH[curr];
    if (currNode) {
      if (currNode.tier === "tier-0") tier0Impacted = true;
      if (depth > 1) transitiveDependents.push(curr);

      if (depth < maxDepth) {
        for (const nextDep of currNode.dependents) {
          if (!visited.has(nextDep)) {
            queue.push([nextDep, depth + 1]);
          }
        }
      }
    } else {
      if (depth > 1) transitiveDependents.push(curr);
    }
  }

  const totalAffected = directDependents.length + transitiveDependents.length;
  let riskRating = "LOW";
  if (tier0Impacted) {
    riskRating = "CRITICAL";
  } else if (totalAffected > 3 || node.tier === "tier-1") {
    riskRating = totalAffected > 3 ? "HIGH" : "MEDIUM";
  }

  return {
    target_service: serviceName,
    found: true,
    service_tier: node.tier,
    owner: node.owner,
    direct_dependents: directDependents,
    transitive_dependents: transitiveDependents,
    total_affected_services: totalAffected,
    tier_0_impacted: tier0Impacted,
    risk_rating: riskRating,
  };
}

// Redaction of Sensitive Data
export function redactSensitiveData(data: any): any {
  if (typeof data === "string") {
    let s = data;
    s = s.replace(/sk-[a-zA-Z0-9_-]{10,}/g, "[REDACTED_API_KEY]");
    s = s.replace(/Bearer\s+[a-zA-Z0-9_\-\.]{15,}/gi, "Bearer [REDACTED_TOKEN]");
    s = s.replace(
      /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b/g,
      "[REDACTED_EMAIL]"
    );
    s = s.replace(/\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b/g, "[REDACTED_CARD]");
    return s;
  }
  if (Array.isArray(data)) {
    return data.map((item) => redactSensitiveData(item));
  }
  if (data !== null && typeof data === "object") {
    const result: Record<string, any> = {};
    for (const [key, val] of Object.entries(data)) {
      const lower = key.toLowerCase();
      if (
        lower.includes("password") ||
        lower.includes("secret") ||
        lower.includes("token") ||
        lower.includes("auth") ||
        lower.includes("credential") ||
        lower.includes("private_key") ||
        lower.includes("key")
      ) {
        result[key] = "[REDACTED]";
      } else {
        result[key] = redactSensitiveData(val);
      }
    }
    return result;
  }
  return data;
}

// Deterministic Safety Guardrails Engine
export function evaluateSafety(
  proposal: ActionProposal,
  envContext: string = "prod",
  humanApprovalToken?: string
): SafetyDecision {
  const violations: string[] = [];
  const blast = calculateBlastRadius(proposal.service);

  // Check 1: Environment Isolation
  if (envContext !== proposal.environment) {
    violations.push(
      `CROSS_ENVIRONMENT_VIOLATION: Execution session scoped to '${envContext}' cannot mutate service in '${proposal.environment}'.`
    );
  }

  // Check 2: Scaling Invariants
  if (proposal.tool_name === "simulate_scale") {
    const reps = proposal.parameters.replicas ?? 0;
    if (reps <= 0) {
      violations.push("INVARIANT_VIOLATION: Cannot scale replicas to <= 0 during incident.");
    }
    if (reps > 6) {
      violations.push("INVARIANT_VIOLATION: Replicas exceed max safety threshold of 6.");
    }
  }

  // Check 3: Autonomy Tier Classification
  let requiredTier: AutonomyTier = 2;
  const directDepCount = blast.direct_dependents.length;
  const totalAffected = blast.total_affected_services;

  if (
    proposal.service === "auth-service" ||
    blast.tier_0_impacted ||
    blast.service_tier === "tier-0" ||
    directDepCount > 2 ||
    totalAffected > 4
  ) {
    requiredTier = 3;
  } else if (
    proposal.tool_name === "simulate_restart" &&
    blast.service_tier !== "tier-0" &&
    directDepCount <= 2
  ) {
    requiredTier = 2;
  } else {
    requiredTier = 3;
  }

  const hasViolations = violations.length > 0;
  const requiresHuman = requiredTier === 3 && !hasViolations;
  const hasValidToken = Boolean(
    humanApprovalToken && humanApprovalToken.startsWith("TOKEN-HUMAN-APPROVED-")
  );

  const approved = !hasViolations && (!requiresHuman || hasValidToken);

  let rationale = `Autonomy Tier ${requiredTier} evaluation complete.`;
  if (hasViolations) {
    rationale = `REJECTED: ${violations.join(" ")}`;
  } else if (requiresHuman && !hasValidToken) {
    rationale = `AWAITING_APPROVAL: Tier-3 action requires human authorization token.`;
  } else if (requiresHuman && hasValidToken) {
    rationale = `APPROVED: Authorized via validated human token.`;
  } else {
    rationale = `APPROVED: Autonomous execution permitted under Tier-2 criteria.`;
  }

  return {
    action_id: proposal.action_id,
    approved,
    tier: requiredTier,
    requires_human_token: requiresHuman && !hasValidToken,
    policy_violations: violations,
    blast_radius_info: {
      target_service: blast.target_service,
      service_tier: blast.service_tier,
      direct_dependents: blast.direct_dependents,
      transitive_dependents: blast.transitive_dependents,
      total_affected_services: blast.total_affected_services,
      risk_rating: blast.risk_rating,
    },
    audit_rationale: rationale,
  };
}

// Full Multi-Agent Simulation Workflow
export function runSimulationWorkflow(
  incident: Incident,
  humanApprovalToken?: string,
  envContext: string = "prod"
): SimulationResult {
  const runId = "RUN-" + Math.random().toString(36).substring(2, 10).toUpperCase();
  const steps: ExecutionStep[] = [];
  let totalTokens = 0;
  let totalLatency = 0;

  // Baseline telemetry
  const isAuth = incident.service === "auth-service";
  const telemetryBefore = {
    status: "DEGRADED",
    memory_utilization_pct: isAuth ? 72.4 : 96.2,
    cpu_utilization_pct: isAuth ? 94.0 : 88.5,
    p99_latency_ms: isAuth ? 3200 : 4820,
    error_rate_pct: isAuth ? 14.8 : 19.4,
    replicas: isAuth ? 4 : 3,
  };

  // STEP 1: TRIAGE -> PLANNING (Planner Agent)
  const step1Latency = 12;
  const step1Tokens = 350;
  totalTokens += step1Tokens;
  totalLatency += step1Latency;

  const symptoms = [
    isAuth ? "REDIS_CONNECTION_EXHAUSTION" : "MEMORY_EXHAUSTION_OR_LEAK",
    "LATENCY_SPIKE_OR_TIMEOUT",
    "HIGH_ERROR_RATE",
  ];

  const planMsg: A2AMessage = {
    message_id: "MSG-" + Math.random().toString(36).substring(2, 8),
    correlation_id: runId,
    sender: "planner",
    recipient: "investigator",
    message_type: "TASK_DELEGATION",
    timestamp: new Date().toISOString(),
    payload: {
      incident_id: incident.id,
      service: incident.service,
      symptoms,
      delegated_tasks: [
        `Fetch container stderr/stdout logs for ${incident.service}`,
        `Sample p99 latency & resource metrics for ${incident.service}`,
        `Query Hybrid RAG for runbook procedures on ${symptoms[0]}`,
        "Assess blast radius in service dependency graph",
      ],
    },
  };

  steps.push({
    step_number: 1,
    timestamp: new Date().toISOString(),
    agent: "planner",
    action: "DECOMPOSE_INCIDENT_AND_SET_GOALS",
    state_transition: { from: "TRIAGE", to: "PLANNING" },
    latency_ms: step1Latency,
    tokens_used: step1Tokens,
    decisions: [
      `Triaged incident ${incident.id} for service '${incident.service}' in environment '${incident.environment}'.`,
      `Extracted high-priority symptoms: ${symptoms.join(", ")}.`,
      `Generated 4 structured diagnostic subtasks and delegated to Investigator Agent via A2A message.`,
    ],
    message: planMsg,
  });

  // STEP 2: INVESTIGATING (Investigator Agent)
  const step2Latency = 38;
  const step2Tokens = 520;
  totalTokens += step2Tokens;
  totalLatency += step2Latency;

  const ragHits = searchHybridRAG(
    `${incident.service} ${incident.description}`,
    incident.service,
    2
  );

  const evidenceMsg: A2AMessage = {
    message_id: "MSG-" + Math.random().toString(36).substring(2, 8),
    correlation_id: runId,
    sender: "investigator",
    recipient: "ops",
    message_type: "EVIDENCE_REPORT",
    timestamp: new Date().toISOString(),
    payload: {
      service: incident.service,
      identified_root_cause: isAuth
        ? "REDIS_CONNECTION_POOL_EXHAUSTION"
        : "MEMORY_LEAK_HEAP_EXHAUSTION",
      citations: ragHits,
      telemetry: telemetryBefore,
    },
  };

  steps.push({
    step_number: 2,
    timestamp: new Date().toISOString(),
    agent: "investigator",
    action: "TELEMETRY_GATHERING_AND_HYBRID_RAG",
    state_transition: { from: "PLANNING", to: "INVESTIGATING" },
    latency_ms: step2Latency,
    tokens_used: step2Tokens,
    decisions: [
      `Invoked MCP tools 'get_logs' and 'get_metrics' for ${incident.service}.`,
      `Executed Hybrid RAG (Okapi BM25 + Dense vector search with Reciprocal Rank Fusion).`,
      `Grounded findings in Runbook ${ragHits[0]?.doc_id || "DOC-RB-PAY-001"} (RRF: ${ragHits[0]?.rrf_score}).`,
      `Synthesized diagnostic evidence report and dispatched to Ops Agent.`,
    ],
    message: evidenceMsg,
    details: { rag_citations: ragHits },
  });

  // STEP 3: PROPOSING (Ops Agent)
  const step3Latency = 24;
  const step3Tokens = 410;
  totalTokens += step3Tokens;
  totalLatency += step3Latency;

  const proposedTool = isAuth ? "simulate_restart" : "simulate_restart";
  const proposal: ActionProposal = {
    action_id: "ACT-" + Math.random().toString(36).substring(2, 8).toUpperCase(),
    tool_name: proposedTool,
    service: incident.service,
    environment: incident.environment,
    parameters: {
      service: incident.service,
      reason: `Automated remediation grounded in runbook ${ragHits[0]?.doc_id || "DOC-RB-PAY-001"}.`,
    },
    reasoning: `Runbook recommends rolling restart to clear thread and heap buffers.`,
    evidence_citations: ragHits.map((h) => ({
      doc_id: h.doc_id,
      title: h.title,
      score: h.rrf_score,
      snippet: h.citation_snippet,
    })),
    rejected_alternatives: [
      {
        tool_name: "simulate_scale",
        parameters: { service: incident.service, replicas: 8 },
        rejection_reason:
          "Scaling out with active leak multiplies heap exhaustion across additional nodes.",
      },
      {
        tool_name: "simulate_scale",
        parameters: { service: incident.service, replicas: 1 },
        rejection_reason:
          "Downscaling during incident violates minimum availability invariant.",
      },
    ],
    estimated_blast_radius: isAuth ? 3 : 2,
    autonomy_tier: isAuth ? 3 : 2,
  };

  const proposalMsg: A2AMessage = {
    message_id: "MSG-" + Math.random().toString(36).substring(2, 8),
    correlation_id: runId,
    sender: "ops",
    recipient: "verifier",
    message_type: "ACTION_PROPOSAL",
    timestamp: new Date().toISOString(),
    payload: { proposal },
  };

  steps.push({
    step_number: 3,
    timestamp: new Date().toISOString(),
    agent: "ops",
    action: "PROPOSE_REMEDIATION_ACTION",
    state_transition: { from: "INVESTIGATING", to: "PROPOSING" },
    latency_ms: step3Latency,
    tokens_used: step3Tokens,
    decisions: [
      `Formulated action proposal ${proposal.action_id} to invoke '${proposal.tool_name}'.`,
      `Grounded in runbook ${ragHits[0]?.doc_id || "DOC-RB-PAY-001"}.`,
      `Evaluated and recorded 2 rejected alternative remediation actions with explicit rationale.`,
      `Forwarded proposal to Verifier Agent for deterministic safety gate audit.`,
    ],
    message: proposalMsg,
    details: { proposal },
  });

  // STEP 4: SAFETY VERIFY (Verifier Agent)
  const step4Latency = 16;
  const step4Tokens = 310;
  totalTokens += step4Tokens;
  totalLatency += step4Latency;

  const safetyDecision = evaluateSafety(proposal, envContext, humanApprovalToken);

  const verifierMsg: A2AMessage = {
    message_id: "MSG-" + Math.random().toString(36).substring(2, 8),
    correlation_id: runId,
    sender: "verifier",
    recipient: "orchestrator",
    message_type: "SAFETY_DECISION",
    timestamp: new Date().toISOString(),
    payload: { safety_decision: safetyDecision },
  };

  steps.push({
    step_number: 4,
    timestamp: new Date().toISOString(),
    agent: "verifier",
    action: "AUDIT_SAFETY_AND_BLAST_RADIUS",
    state_transition: { from: "PROPOSING", to: "SAFETY_VERIFY" },
    latency_ms: step4Latency,
    tokens_used: step4Tokens,
    decisions: [
      `Evaluated 6 deterministic safety guardrails.`,
      `Blast radius calculated via BFS: ${safetyDecision.blast_radius_info.total_affected_services} dependent services.`,
      `Autonomy Tier requirement: Tier ${safetyDecision.tier}.`,
      safetyDecision.approved
        ? `Verdict: APPROVED for execution.`
        : safetyDecision.requires_human_token
        ? `Verdict: HELD IN AWAITING_APPROVAL (Tier-3 human token required).`
        : `Verdict: REJECTED due to invariant violation (${safetyDecision.policy_violations.join(", ")}).`,
    ],
    message: verifierMsg,
    details: { safetyDecision },
  });

  // STEP 5: EXECUTION OR TERMINATION
  let finalState: "COMPLETED" | "AWAITING_APPROVAL" | "FAILED" = "COMPLETED";
  let executionResult: { ok: boolean; status: string; message: string } | undefined;
  let telemetryAfter: any = undefined;

  if (safetyDecision.approved) {
    finalState = "COMPLETED";
    executionResult = {
      ok: true,
      status: "SUCCESS",
      message: `Successfully executed rolling restart on ${incident.service}. Memory buffers purged.`,
    };
    telemetryAfter = {
      status: "HEALTHY",
      memory_utilization_pct: 41.5,
      cpu_utilization_pct: 32.0,
      p99_latency_ms: 125,
      error_rate_pct: 0.05,
      replicas: telemetryBefore.replicas,
    };

    steps.push({
      step_number: 5,
      timestamp: new Date().toISOString(),
      agent: "orchestrator",
      action: "EXECUTE_TOOL_VIA_MCP_AND_VERIFY_RECOVERY",
      state_transition: { from: "SAFETY_VERIFY", to: "COMPLETED" },
      latency_ms: 18,
      tokens_used: 120,
      decisions: [
        `Dispatched '${proposal.tool_name}' through MCP Tool Server with environment scope '${envContext}'.`,
        `Tool returned status: SUCCESS.`,
        `Post-action telemetry verified healthy: p99 latency dropped from ${telemetryBefore.p99_latency_ms}ms to ${telemetryAfter.p99_latency_ms}ms.`,
        `Workflow reached terminal state COMPLETED. Audit trace finalized.`,
      ],
      details: { executionResult, telemetryAfter },
    });
  } else if (safetyDecision.requires_human_token) {
    finalState = "AWAITING_APPROVAL";
    steps.push({
      step_number: 5,
      timestamp: new Date().toISOString(),
      agent: "orchestrator",
      action: "HOLD_FOR_HUMAN_APPROVAL",
      state_transition: { from: "SAFETY_VERIFY", to: "AWAITING_APPROVAL" },
      latency_ms: 5,
      tokens_used: 40,
      decisions: [
        `Action touches Tier-0 service or high blast radius.`,
        `Autonomous execution prohibited by Tier-3 policy gate.`,
        `Workflow paused in state AWAITING_APPROVAL awaiting human authorization token.`,
      ],
    });
  } else {
    finalState = "FAILED";
    steps.push({
      step_number: 5,
      timestamp: new Date().toISOString(),
      agent: "orchestrator",
      action: "TERMINATE_WORKFLOW_ON_VIOLATION",
      state_transition: { from: "SAFETY_VERIFY", to: "FAILED" },
      latency_ms: 5,
      tokens_used: 40,
      decisions: [
        `Hard policy violation detected: ${safetyDecision.policy_violations.join(", ")}.`,
        `Execution rejected without invoking mutating tools.`,
        `Workflow halted in terminal state FAILED.`,
      ],
    });
  }

  const totalCostUsd = (totalTokens / 1000) * 0.002;

  return {
    run_id: runId,
    incident,
    final_state: finalState,
    status:
      finalState === "COMPLETED"
        ? "SUCCESS"
        : finalState === "AWAITING_APPROVAL"
        ? "AWAITING_APPROVAL"
        : "FAILED",
    proposal,
    safety_decision: safetyDecision,
    execution_result: executionResult,
    telemetry_before: telemetryBefore,
    telemetry_after: telemetryAfter,
    steps,
    total_tokens: totalTokens,
    total_latency_ms: totalLatency,
    total_cost_usd: Number(totalCostUsd.toFixed(5)),
  };
}
