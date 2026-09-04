import React, { useState } from "react";
import {
  ShieldAlert,
  Lock,
  EyeOff,
  CheckCircle2,
  AlertTriangle,
  Play,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import { SERVICES_GRAPH } from "../data/platformData";
import { evaluateSafety, redactSensitiveData } from "../utils/engine";
import { ActionProposal } from "../types";

export const SafetyView: React.FC = () => {
  // Guardrail tester state
  const [testService, setTestService] = useState<string>("payment-service");
  const [testEnv, setTestEnv] = useState<string>("prod");
  const [testSessionEnv, setTestSessionEnv] = useState<string>("prod");
  const [testTool, setTestTool] = useState<string>("simulate_restart");
  const [testReplicas, setTestReplicas] = useState<number>(3);
  const [testToken, setTestToken] = useState<string>("");
  const [evalResult, setEvalResult] = useState<any>(null);

  // Redaction tester state
  const [rawText, setRawText] = useState<string>(
    JSON.stringify(
      {
        incident_id: "INC-9102",
        author_email: "sre-oncall@corp.internal",
        cluster_auth: "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        admin_password: "SuperSecretPassword123!",
        stripe_api_key: "sk-live-51Mz09485098234jksdf890",
        credit_card_test: "4111-2222-3333-4444",
        safe_parameter: "rolling-restart-v2",
      },
      null,
      2
    )
  );
  const [redactedText, setRedactedText] = useState<string>("");

  const handleEvaluateSafety = () => {
    const mockProposal: ActionProposal = {
      action_id: "TEST-ACT-" + Math.floor(1000 + Math.random() * 9000),
      tool_name: testTool,
      service: testService,
      environment: testEnv,
      parameters:
        testTool === "simulate_scale"
          ? { service: testService, replicas: testReplicas }
          : { service: testService, reason: "Manual safety test" },
      reasoning: "Test of deterministic safety guardrails",
      evidence_citations: [],
      rejected_alternatives: [],
      estimated_blast_radius: 2,
      autonomy_tier: 2,
    };

    const dec = evaluateSafety(mockProposal, testSessionEnv, testToken || undefined);
    setEvalResult(dec);
  };

  const handleRedact = () => {
    try {
      const parsed = JSON.parse(rawText);
      const scrubbed = redactSensitiveData(parsed);
      setRedactedText(JSON.stringify(scrubbed, null, 2));
    } catch {
      const scrubbedStr = redactSensitiveData(rawText);
      setRedactedText(scrubbedStr);
    }
  };

  return (
    <div className="space-y-6">
      {/* 3-Tier Autonomy Matrix Spec */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
        <div className="flex items-center gap-2 border-b border-slate-100 pb-3">
          <ShieldAlert className="w-5 h-5 text-emerald-600" />
          <div>
            <h2 className="text-sm font-semibold text-slate-900">
              Deterministic 3-Tier Autonomy & Permission Matrix
            </h2>
            <p className="text-xs text-slate-500">
              Operational actions are strictly categorized by risk, service tier, and blast radius.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="border border-emerald-200 bg-emerald-50/40 rounded-lg p-4 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold font-mono px-2 py-0.5 rounded bg-emerald-100 text-emerald-800">
                TIER 1: READ-ONLY
              </span>
              <span className="text-[11px] font-mono text-emerald-700 font-semibold">
                Autonomous
              </span>
            </div>
            <div className="text-xs font-medium text-slate-900">
              Telemetry & Topology Discovery
            </div>
            <p className="text-xs text-slate-600">
              Tools: <code>get_logs</code>, <code>get_metrics</code>,{" "}
              <code>get_dependency_graph</code>. Always permitted autonomously without approval.
            </p>
          </div>

          <div className="border border-blue-200 bg-blue-50/40 rounded-lg p-4 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold font-mono px-2 py-0.5 rounded bg-blue-100 text-blue-800">
                TIER 2: VERIFIED LOW-RISK
              </span>
              <span className="text-[11px] font-mono text-blue-700 font-semibold">
                Guarded Autonomy
              </span>
            </div>
            <div className="text-xs font-medium text-slate-900">
              Low Blast Radius Mutations
            </div>
            <p className="text-xs text-slate-600">
              Tools: <code>simulate_restart</code> on tier-1/tier-2 services with ≤ 2 direct dependents.
              Allowed autonomously ONLY IF all guardrail invariants pass.
            </p>
          </div>

          <div className="border border-amber-200 bg-amber-50/40 rounded-lg p-4 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold font-mono px-2 py-0.5 rounded bg-amber-100 text-amber-800">
                TIER 3: HIGH-RISK / TIER-0
              </span>
              <span className="text-[11px] font-mono text-amber-700 font-semibold">
                Human-in-the-Loop
              </span>
            </div>
            <div className="text-xs font-medium text-slate-900">
              Critical Services & High Blast Radius
            </div>
            <p className="text-xs text-slate-600">
              Touches Tier-0 (e.g. <code>auth-service</code>), &gt; 2 dependents, or wide scale.
              Strictly requires cryptographic/session human approval token.
            </p>
          </div>
        </div>
      </div>

      {/* Interactive Guardrail Evaluation Tester */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <Lock className="w-5 h-5 text-indigo-600" />
            <h3 className="text-sm font-semibold text-slate-900">
              Interactive Guardrails Policy Tester
            </h3>
          </div>
          <span className="text-xs font-mono text-slate-400">
            Engine: 6 Deterministic Policy Gates
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Target Service
            </label>
            <select
              value={testService}
              onChange={(e) => setTestService(e.target.value)}
              className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-2 bg-white text-slate-900"
            >
              {Object.keys(SERVICES_GRAPH).map((svc) => (
                <option key={svc} value={svc}>
                  {svc} ({SERVICES_GRAPH[svc].tier})
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Service Environment
            </label>
            <select
              value={testEnv}
              onChange={(e) => setTestEnv(e.target.value)}
              className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-2 bg-white text-slate-900"
            >
              <option value="prod">prod</option>
              <option value="staging">staging</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Agent Session Environment
            </label>
            <select
              value={testSessionEnv}
              onChange={(e) => setTestSessionEnv(e.target.value)}
              className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-2 bg-white text-slate-900"
            >
              <option value="prod">prod</option>
              <option value="staging">staging (mismatch if target is prod)</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Mutating Tool
            </label>
            <select
              value={testTool}
              onChange={(e) => setTestTool(e.target.value)}
              className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-2 bg-white text-slate-900"
            >
              <option value="simulate_restart">simulate_restart</option>
              <option value="simulate_scale">simulate_scale</option>
            </select>
          </div>
        </div>

        {testTool === "simulate_scale" && (
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Target Replicas (Invariant: 1 to 6)
            </label>
            <input
              type="number"
              value={testReplicas}
              onChange={(e) => setTestReplicas(Number(e.target.value))}
              className="w-32 text-xs font-mono rounded-lg border-slate-300 border px-3 py-1.5 text-slate-900"
            />
          </div>
        )}

        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 pt-1">
          <div className="flex-1">
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Human Authorization Token (Optional)
            </label>
            <input
              type="text"
              value={testToken}
              onChange={(e) => setTestToken(e.target.value)}
              placeholder="e.g. TOKEN-HUMAN-APPROVED-12345"
              className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-1.5 text-slate-900"
            />
          </div>

          <button
            id="run-guardrails-test-btn"
            onClick={handleEvaluateSafety}
            className="flex items-center justify-center gap-2 px-5 py-2.5 rounded-lg bg-slate-900 text-white text-xs font-semibold hover:bg-slate-800 transition-colors self-end shadow-sm"
          >
            <Play className="w-4 h-4 text-emerald-400" />
            Evaluate Guardrails
          </button>
        </div>

        {/* Evaluation Output */}
        {evalResult && (
          <div className="border border-slate-200 rounded-lg p-4 bg-slate-50 space-y-3 mt-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-700">
                Guardrail Decision Result:
              </span>
              <span
                className={`text-xs font-bold font-mono px-2.5 py-1 rounded border ${
                  evalResult.approved
                    ? "bg-emerald-100 text-emerald-800 border-emerald-300"
                    : evalResult.requires_human_token
                    ? "bg-amber-100 text-amber-800 border-amber-300"
                    : "bg-red-100 text-red-800 border-red-300"
                }`}
              >
                {evalResult.approved
                  ? "APPROVED (AUTONOMOUS EXECUTION)"
                  : evalResult.requires_human_token
                  ? "HELD IN AWAITING_APPROVAL (TIER 3)"
                  : "HARD REJECTION (POLICY VIOLATION)"}
              </span>
            </div>

            <div className="text-xs text-slate-700 font-mono">
              Rationale: <strong>{evalResult.audit_rationale}</strong>
            </div>

            {evalResult.policy_violations.length > 0 && (
              <div className="bg-red-50 border border-red-200 rounded-md p-3 text-xs text-red-800 font-mono">
                <div className="font-bold mb-1">Violations:</div>
                <ul className="list-disc pl-4 space-y-1">
                  {evalResult.policy_violations.map((v: string, i: number) => (
                    <li key={i}>{v}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Sensitive Data Redaction Sandbox */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <EyeOff className="w-5 h-5 text-amber-600" />
            <h3 className="text-sm font-semibold text-slate-900">
              Audit Trace Sensitive Data Redaction Sandbox
            </h3>
          </div>
          <span className="text-xs font-mono text-slate-400">
            Scrubbing: Passwords, API Keys, JWT Tokens, Emails, CCs
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-xs font-medium text-slate-700">
                Raw Input JSON / Text (Containing Secrets)
              </label>
            </div>
            <textarea
              rows={8}
              value={rawText}
              onChange={(e) => setRawText(e.target.value)}
              className="w-full text-[11px] font-mono rounded-lg border-slate-300 border p-3 text-slate-900 focus:ring-1 focus:ring-slate-900 focus:border-slate-900"
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-xs font-medium text-slate-700">
                Scrubbed Output (Auditable Sanitized JSON)
              </label>
            </div>
            <textarea
              rows={8}
              readOnly
              value={redactedText || "Click 'Scrub Sensitive Data' below..."}
              className="w-full text-[11px] font-mono rounded-lg border-slate-300 border p-3 bg-slate-50 text-slate-800"
            />
          </div>
        </div>

        <button
          id="scrub-secrets-btn"
          onClick={handleRedact}
          className="flex items-center justify-center gap-2 px-5 py-2 rounded-lg bg-slate-900 text-white text-xs font-semibold hover:bg-slate-800 transition-colors shadow-sm"
        >
          <EyeOff className="w-4 h-4 text-emerald-400" />
          Scrub Sensitive Data
        </button>
      </div>
    </div>
  );
};
