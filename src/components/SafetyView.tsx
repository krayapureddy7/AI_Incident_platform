import React, { useEffect, useState } from "react";
import { ShieldAlert, Lock, EyeOff, Play, Loader2, AlertTriangle } from "lucide-react";
import {
  ApiError,
  SafetyPreviewResponse,
  ServiceGraphNode,
  getHealth,
  listServices,
  previewRedaction,
  previewSafety,
} from "../utils/apiClient";

export const SafetyView: React.FC = () => {
  const [nodes, setNodes] = useState<ServiceGraphNode[]>([]);
  const [sessionEnvironment, setSessionEnvironment] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Guardrail tester state
  const [testService, setTestService] = useState<string>("payment-service");
  const [testEnv, setTestEnv] = useState<string>("prod");
  const [testTool, setTestTool] = useState<string>("simulate_restart");
  const [testReplicas, setTestReplicas] = useState<number>(3);
  const [testToken, setTestToken] = useState<string>("");
  const [evalResult, setEvalResult] = useState<SafetyPreviewResponse | null>(null);
  const [evaluating, setEvaluating] = useState<boolean>(false);
  const [evalError, setEvalError] = useState<string | null>(null);

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
  const [redactError, setRedactError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([listServices(), getHealth()])
      .then(([servicesRes, health]) => {
        setNodes(servicesRes.nodes);
        setSessionEnvironment(health.session_environment);
      })
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : String(err)));
  }, []);

  const handleEvaluateSafety = () => {
    setEvaluating(true);
    setEvalError(null);
    const parameters =
      testTool === "simulate_scale"
        ? { service: testService, replicas: testReplicas }
        : { service: testService, reason: "Manual safety test" };

    previewSafety(testTool, testService, testEnv, parameters, testToken || undefined)
      .then(setEvalResult)
      .catch((err) => setEvalError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setEvaluating(false));
  };

  const handleRedact = () => {
    setRedactError(null);
    let parsed: Record<string, any>;
    try {
      parsed = JSON.parse(rawText);
    } catch {
      setRedactError("Input is not valid JSON.");
      return;
    }
    previewRedaction(parsed)
      .then((res) => setRedactedText(JSON.stringify(res.redacted, null, 2)))
      .catch((err) => setRedactError(err instanceof ApiError ? err.message : String(err)));
  };

  return (
    <div className="space-y-6">
      {loadError && (
        <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          {loadError}
        </div>
      )}

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
            <div className="text-xs font-medium text-slate-900">Low Blast Radius Mutations</div>
            <p className="text-xs text-slate-600">
              Tools: <code>simulate_restart</code> on tier-1/tier-2 services with ≤ 2 direct
              dependents. Allowed autonomously ONLY IF all guardrail invariants pass.
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
              Requires an HMAC-signed approval token bound to this exact action, minted out of
              band (see the CLI's <code>approve --approver</code>) -- never issued from this UI.
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
            Live from POST /safety/preview -- read-only, never dispatches
          </span>
        </div>

        <div className="flex items-center gap-2 text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
          <span>
            Session authority is fixed by the deployment, not by this form:{" "}
            <strong className="font-mono">
              {sessionEnvironment ?? "..."}
            </strong>
            . Pick a different target environment below to see the isolation barrier reject it.
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Target Service
            </label>
            <select
              value={testService}
              onChange={(e) => setTestService(e.target.value)}
              className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-2 bg-white text-slate-900"
            >
              {nodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.id} ({n.tier})
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Proposal's Target Environment
            </label>
            <select
              value={testEnv}
              onChange={(e) => setTestEnv(e.target.value)}
              className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-2 bg-white text-slate-900"
            >
              <option value="prod">prod</option>
              <option value="staging">staging (mismatch if session is prod)</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Mutating Tool</label>
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
              Target Replicas (Invariant: 1 to 6 for unattended execution)
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
              placeholder="Paste a signed token minted via `mini_platform cli approve --approver ...`"
              className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-1.5 text-slate-900"
            />
          </div>

          <button
            id="run-guardrails-test-btn"
            onClick={handleEvaluateSafety}
            disabled={evaluating}
            className="flex items-center justify-center gap-2 px-5 py-2.5 rounded-lg bg-slate-900 text-white text-xs font-semibold hover:bg-slate-800 transition-colors self-end shadow-sm disabled:opacity-50"
          >
            {evaluating ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Play className="w-4 h-4 text-emerald-400" />
            )}
            Evaluate Guardrails
          </button>
        </div>

        {evalError && (
          <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            {evalError}
          </div>
        )}

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

            <div className="text-xs text-slate-700 font-mono">{evalResult.explanation}</div>

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

            <div className="pt-1">
              <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1.5">
                Policy Checks Performed
              </div>
              <div className="space-y-1">
                {evalResult.checks_performed.map((c, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-2 text-[11px] font-mono px-2 py-1 rounded bg-white border border-slate-200"
                  >
                    <span
                      className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                        c.passed ? "bg-emerald-500" : "bg-red-500"
                      }`}
                    />
                    <span className="font-bold text-slate-700">{c.policy}</span>
                    {c.reason && <span className="text-slate-500 truncate">{c.reason}</span>}
                  </div>
                ))}
              </div>
            </div>
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
            Live from POST /safety/redact-preview
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-xs font-medium text-slate-700">
                Raw Input JSON (Containing Secrets)
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

        {redactError && (
          <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            {redactError}
          </div>
        )}

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
