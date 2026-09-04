import React, { useState } from "react";
import {
  FileText,
  Layers,
  Scale,
  Presentation,
  CheckCircle2,
  Code2,
} from "lucide-react";

export const DocsView: React.FC = () => {
  const [docTab, setDocTab] = useState<"arch" | "tradeoffs" | "slides" | "version">("arch");

  return (
    <div className="space-y-6">
      {/* Sub-navigation */}
      <div className="flex space-x-2 border-b border-slate-200 pb-2">
        <button
          onClick={() => setDocTab("arch")}
          className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-semibold transition-colors ${
            docTab === "arch"
              ? "bg-slate-900 text-white"
              : "bg-slate-100 text-slate-700 hover:bg-slate-200"
          }`}
        >
          <Layers className="w-4 h-4" />
          Architecture Specification
        </button>

        <button
          onClick={() => setDocTab("tradeoffs")}
          className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-semibold transition-colors ${
            docTab === "tradeoffs"
              ? "bg-slate-900 text-white"
              : "bg-slate-100 text-slate-700 hover:bg-slate-200"
          }`}
        >
          <Scale className="w-4 h-4" />
          Engineering Trade-Offs
        </button>

        <button
          onClick={() => setDocTab("slides")}
          className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-semibold transition-colors ${
            docTab === "slides"
              ? "bg-slate-900 text-white"
              : "bg-slate-100 text-slate-700 hover:bg-slate-200"
          }`}
        >
          <Presentation className="w-4 h-4" />
          Presentation Slides
        </button>

        <button
          onClick={() => setDocTab("version")}
          className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-semibold transition-colors ${
            docTab === "version"
              ? "bg-slate-900 text-white"
              : "bg-slate-100 text-slate-700 hover:bg-slate-200"
          }`}
        >
          <Code2 className="w-4 h-4" />
          Version Manifest
        </button>
      </div>

      {/* Architecture Spec View */}
      {docTab === "arch" && (
        <div className="bg-white border border-slate-200 rounded-xl p-6 shadow-sm space-y-5">
          <div className="border-b border-slate-100 pb-4">
            <h2 className="text-lg font-bold text-slate-900">
              System Architecture & Multi-Agent Design
            </h2>
            <p className="text-xs text-slate-500 mt-1">
              Deterministic finite state machine orchestration with strict agent role segregation and safety gates.
            </p>
          </div>

          <div className="space-y-4 text-xs text-slate-700 leading-relaxed font-sans">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="p-4 rounded-lg bg-slate-50 border border-slate-200 space-y-2">
                <div className="font-bold text-slate-900 text-sm">
                  1. Multi-Agent Separation of Concerns
                </div>
                <ul className="list-disc pl-4 space-y-1">
                  <li>
                    <strong>Planner Agent:</strong> Decomposes raw incident alerts, extracts symptoms, and generates diagnostic subtasks.
                  </li>
                  <li>
                    <strong>Investigator Agent:</strong> Interacts with MCP read-only tools, queries Hybrid RAG, and synthesizes root cause evidence.
                  </li>
                  <li>
                    <strong>Ops Agent:</strong> Proposes concrete operational remediation actions, grounds them in runbook citations, and explicitly evaluates rejected alternatives.
                  </li>
                  <li>
                    <strong>Verifier Agent:</strong> Evaluates deterministic guardrails, blast radius, and assigns autonomy tier before any mutation is dispatched.
                  </li>
                </ul>
              </div>

              <div className="p-4 rounded-lg bg-slate-50 border border-slate-200 space-y-2">
                <div className="font-bold text-slate-900 text-sm">
                  2. Finite State Machine Orchestrator
                </div>
                <ul className="list-disc pl-4 space-y-1">
                  <li>
                    <strong>Strict Lifecycle:</strong> <code>IDLE → TRIAGE → PLANNING → INVESTIGATING → PROPOSING → SAFETY_VERIFY → EXECUTING → COMPLETED</code>.
                  </li>
                  <li>
                    <strong>Halt States:</strong> Pauses in <code>AWAITING_APPROVAL</code> when human token is needed, or aborts to <code>FAILED</code> on invariant violation.
                  </li>
                  <li>
                    <strong>Audit Trail:</strong> Records immutable execution trajectory with latency, tokens, decisions, and sensitive data redaction.
                  </li>
                </ul>
              </div>
            </div>

            <div className="p-4 rounded-lg bg-slate-50 border border-slate-200 space-y-2">
              <div className="font-bold text-slate-900 text-sm">
                3. Knowledge Retrieval & Blast Radius Traversal
              </div>
              <p>
                The platform integrates <strong>Hybrid RAG</strong> combining Okapi BM25 lexical keyword retrieval with dense semantic vector embeddings fused via <strong>Reciprocal Rank Fusion (RRF, k=60)</strong>. The <strong>Knowledge Graph</strong> provides a directed service mesh topology evaluated via Breadth-First Search (BFS) to calculate cascading downstream impact before permitting execution.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Trade-Offs View */}
      {docTab === "tradeoffs" && (
        <div className="bg-white border border-slate-200 rounded-xl p-6 shadow-sm space-y-5">
          <div className="border-b border-slate-100 pb-4">
            <h2 className="text-lg font-bold text-slate-900">
              Key Engineering Decisions & Production Trade-Offs
            </h2>
            <p className="text-xs text-slate-500 mt-1">
              Deliberate architectural choices balancing determinism, latency, safety, and operational resilience.
            </p>
          </div>

          <div className="space-y-4">
            <div className="p-4 rounded-lg bg-slate-50 border border-slate-200 space-y-1.5">
              <div className="text-xs font-bold text-slate-900">
                Decision 1: Deterministic FSM vs. Autonomous Dynamic Loop
              </div>
              <p className="text-xs text-slate-600 leading-relaxed">
                <strong>Choice:</strong> Implemented a deterministic state machine orchestrator rather than an unconstrained autonomous loop.
                <br />
                <strong>Rationale:</strong> In production incident remediation, unconstrained agent loops risk runaway mutations, circular debugging loops, and unpredictable blast radiuses. An explicit FSM provides predictable transitions, formal invariant enforcement, and zero hallucinatory tool chaining.
              </p>
            </div>

            <div className="p-4 rounded-lg bg-slate-50 border border-slate-200 space-y-1.5">
              <div className="text-xs font-bold text-slate-900">
                Decision 2: 3-Tier Autonomy Gate vs. Binary Autonomous/Manual
              </div>
              <p className="text-xs text-slate-600 leading-relaxed">
                <strong>Choice:</strong> Enforced Tier 1 (Read-only), Tier 2 (Verified low-risk mutations), and Tier 3 (Tier-0 / High blast radius requiring human token).
                <br />
                <strong>Rationale:</strong> A binary gate either creates alert fatigue (demanding approvals for harmless diagnostics) or leaves critical tier-0 services unprotected. The 3-tier model delivers maximum automation for routine recovery while strictly walling off mission-critical infrastructure.
              </p>
            </div>

            <div className="p-4 rounded-lg bg-slate-50 border border-slate-200 space-y-1.5">
              <div className="text-xs font-bold text-slate-900">
                Decision 3: Reciprocal Rank Fusion (RRF) for Hybrid RAG
              </div>
              <p className="text-xs text-slate-600 leading-relaxed">
                <strong>Choice:</strong> Combined BM25 lexical search with dense vector embeddings via RRF.
                <br />
                <strong>Rationale:</strong> Exact error tokens (e.g. <code>OutOfMemoryError</code>, <code>exit code 137</code>, <code>RedisTimeoutException</code>) demand exact lexical matching, whereas operational intentions need semantic similarity. Pure vector search misses exact error strings; pure BM25 misses conceptual synonyms.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Slides View */}
      {docTab === "slides" && (
        <div className="bg-white border border-slate-200 rounded-xl p-6 shadow-sm space-y-5">
          <div className="border-b border-slate-100 pb-4">
            <h2 className="text-lg font-bold text-slate-900">
              Executive & Technical Presentation Deck
            </h2>
            <p className="text-xs text-slate-500 mt-1">
              5-slide overview of the Mini Agentic AI Platform architecture and evaluation results.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="border border-slate-200 rounded-lg p-4 bg-slate-50 space-y-2">
              <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-slate-200 text-slate-800">
                SLIDE 1: MISSION & CHALLENGE
              </span>
              <div className="text-xs font-bold text-slate-900">
                Autonomous Incident Remediation With Zero Production Risk
              </div>
              <p className="text-xs text-slate-600">
                Solving the engineering bottleneck: PagerDuty alerts spike mean-time-to-recovery (MTTR), but unconstrained LLMs cannot safely touch production without strict boundary verification.
              </p>
            </div>

            <div className="border border-slate-200 rounded-lg p-4 bg-slate-50 space-y-2">
              <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-slate-200 text-slate-800">
                SLIDE 2: 4-AGENT TOPOLOGY
              </span>
              <div className="text-xs font-bold text-slate-900">
                Separation of Responsibilities & A2A Envelopes
              </div>
              <p className="text-xs text-slate-600">
                Segregation of powers: Planner (diagnosis), Investigator (evidence), Ops (proposal & rejected alternatives), Verifier (deterministic safety gate).
              </p>
            </div>

            <div className="border border-slate-200 rounded-lg p-4 bg-slate-50 space-y-2">
              <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-slate-200 text-slate-800">
                SLIDE 3: SAFETY & BLAST RADIUS
              </span>
              <div className="text-xs font-bold text-slate-900">
                Knowledge Graph BFS & 3-Tier Autonomy Matrix
              </div>
              <p className="text-xs text-slate-600">
                Automated traversal of service dependencies prevents cascading outages. Tier-0 core services require human cryptographic approval tokens.
              </p>
            </div>

            <div className="border border-slate-200 rounded-lg p-4 bg-slate-50 space-y-2">
              <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-slate-200 text-slate-800">
                SLIDE 4: CONTINUOUS EVALUATION
              </span>
              <div className="text-xs font-bold text-slate-900">
                100% Invariant Pass Rate Across 3 Benchmark Scenarios
              </div>
              <p className="text-xs text-slate-600">
                Verified: Payment OOM auto-remediation, Auth Tier-0 safety block, and Cross-Environment mutation prevention.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Version Manifest View */}
      {docTab === "version" && (
        <div className="bg-white border border-slate-200 rounded-xl p-6 shadow-sm space-y-4">
          <div className="border-b border-slate-100 pb-3">
            <h2 className="text-sm font-semibold text-slate-900">
              System Component Manifest (VERSION.json)
            </h2>
          </div>

          <pre className="text-xs font-mono bg-slate-900 text-slate-100 p-4 rounded-lg overflow-x-auto">
            {JSON.stringify(
              {
                platform_name: "mini_agentic_ai_platform",
                version: "1.2.0",
                architecture: "Deterministic FSM Multi-Agent Orchestration",
                protocol: "Model Context Protocol (MCP) v2024-11-05-local",
                components: {
                  orchestrator: { version: "1.2.0", type: "finite_state_machine" },
                  agents: {
                    planner: "1.1.0",
                    investigator: "1.1.0",
                    ops: "1.2.0",
                    verifier: "1.3.0",
                  },
                  safety: {
                    guardrails_engine: "1.3.0",
                    autonomy_tiers: [1, 2, 3],
                    blast_radius_bfs: "1.1.0",
                    redaction_engine: "1.1.0",
                  },
                  knowledge: {
                    hybrid_rag: "1.2.0",
                    fusion_algorithm: "Reciprocal Rank Fusion (k=60)",
                    lexical_engine: "Okapi BM25",
                    dense_vector_dim: 256,
                  },
                  mcp_tools: [
                    "get_logs",
                    "get_metrics",
                    "get_dependency_graph",
                    "simulate_restart",
                    "simulate_scale",
                  ],
                  evaluation_suite: {
                    version: "1.1.0",
                    benchmark_scenarios: 3,
                    pass_rate: "100.0%",
                  },
                },
              },
              null,
              2
            )}
          </pre>
        </div>
      )}
    </div>
  );
};
