import React, { useState } from "react";
import {
  Play,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Clock,
  ArrowRight,
  ShieldAlert,
  Server,
  Terminal,
  Activity,
  FileCode2,
  RotateCcw,
  KeyRound,
  FileSearch,
} from "lucide-react";
import {
  Incident,
  SimulationResult,
  WorkflowState,
  ExecutionStep,
} from "../types";
import { BENCHMARK_SCENARIOS_DATA, SERVICES_GRAPH } from "../data/platformData";
import { runSimulationWorkflow } from "../utils/engine";

export const IncidentStudio: React.FC = () => {
  const [selectedScenarioIdx, setSelectedScenarioIdx] = useState<number>(0);
  const [service, setService] = useState<string>("payment-service");
  const [envContext, setEnvContext] = useState<string>("prod");
  const [severity, setSeverity] = useState<"SEV-1" | "SEV-2" | "SEV-3">("SEV-1");
  const [description, setDescription] = useState<string>(
    "payment-service memory utilization at 96.2%, multiple OutOfMemoryError exceptions in logs, 503 errors on checkout gateway."
  );
  const [humanToken, setHumanToken] = useState<string>("");
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [isExecuting, setIsExecuting] = useState<boolean>(false);
  const [selectedStep, setSelectedStep] = useState<number>(0);

  // Load benchmark scenario
  const handleLoadScenario = (idx: number) => {
    setSelectedScenarioIdx(idx);
    const sc = BENCHMARK_SCENARIOS_DATA[idx];
    setService(sc.incident.service);
    setEnvContext(sc.env_context || sc.incident.environment);
    setSeverity(sc.incident.severity);
    setDescription(sc.incident.description);
    if (sc.id === "SCENARIO-02-AUTH-TIER0-BLOCK") {
      setHumanToken(""); // Default no token to demonstrate Tier-3 gate
    }
  };

  const handleRunIncident = () => {
    setIsExecuting(true);
    const incident: Incident = {
      id: "INC-" + Math.floor(1000 + Math.random() * 9000),
      title: `${service} Operational Alert (${severity})`,
      description,
      service,
      environment: envContext,
      severity,
    };

    // Simulate realistic execution delay
    setTimeout(() => {
      const res = runSimulationWorkflow(incident, humanToken || undefined, envContext);
      setResult(res);
      setSelectedStep(res.steps.length - 1);
      setIsExecuting(false);
    }, 450);
  };

  const workflowStages: WorkflowState[] = [
    "TRIAGE",
    "PLANNING",
    "INVESTIGATING",
    "PROPOSING",
    "SAFETY_VERIFY",
    "EXECUTING",
    "COMPLETED",
  ];

  return (
    <div className="space-y-6">
      {/* Scenario Presets Banner */}
      <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 sm:p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-900 tracking-tight">
              Pre-Configured Benchmark Scenarios
            </h2>
            <p className="text-xs text-slate-500">
              Select an incident scenario to verify autonomy tiers, blast radius, and invariant guardrails.
            </p>
          </div>
          <span className="text-xs font-mono text-slate-400">
            Suite: Trajectory & Invariant Gate
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {BENCHMARK_SCENARIOS_DATA.map((sc, idx) => {
            const isSelected = selectedScenarioIdx === idx;
            return (
              <button
                key={sc.id}
                id={`scenario-preset-btn-${idx}`}
                onClick={() => handleLoadScenario(idx)}
                className={`text-left p-3.5 rounded-lg border transition-all ${
                  isSelected
                    ? "bg-white border-slate-900 shadow-sm ring-1 ring-slate-900"
                    : "bg-white/60 border-slate-200 hover:border-slate-300 hover:bg-white"
                }`}
              >
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-[11px] font-mono font-medium px-2 py-0.5 rounded bg-slate-100 text-slate-700">
                    {sc.id.split("-")[1]}
                  </span>
                  <span
                    className={`text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded ${
                      sc.expected_trajectory.expected_final_state === "COMPLETED"
                        ? "bg-emerald-100 text-emerald-800"
                        : sc.expected_trajectory.expected_final_state === "AWAITING_APPROVAL"
                        ? "bg-amber-100 text-amber-800"
                        : "bg-red-100 text-red-800"
                    }`}
                  >
                    Expected: {sc.expected_trajectory.expected_final_state}
                  </span>
                </div>
                <div className="text-xs font-medium text-slate-900 line-clamp-1 mb-1">
                  {sc.name}
                </div>
                <div className="text-[11px] text-slate-500 line-clamp-2">
                  {sc.incident.description}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Incident Input & Controls */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-blue-600" />
            <h3 className="text-sm font-semibold text-slate-900">
              Incident Definition & Session Context
            </h3>
          </div>
          <div className="text-xs font-mono text-slate-400">
            A2A Correlation: AUTO-GENERATED
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Target Service
            </label>
            <select
              id="select-service"
              value={service}
              onChange={(e) => setService(e.target.value)}
              className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-2 bg-white text-slate-900 focus:ring-1 focus:ring-slate-900 focus:border-slate-900"
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
              Session Environment Scope
            </label>
            <select
              id="select-env"
              value={envContext}
              onChange={(e) => setEnvContext(e.target.value)}
              className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-2 bg-white text-slate-900 focus:ring-1 focus:ring-slate-900 focus:border-slate-900"
            >
              <option value="prod">prod (Production)</option>
              <option value="staging">staging (Pre-release Sandbox)</option>
              <option value="dev">dev (Local Development)</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Severity Level
            </label>
            <select
              id="select-severity"
              value={severity}
              onChange={(e) => setSeverity(e.target.value as any)}
              className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-2 bg-white text-slate-900 focus:ring-1 focus:ring-slate-900 focus:border-slate-900"
            >
              <option value="SEV-1">SEV-1 (Critical Outage / Degradation)</option>
              <option value="SEV-2">SEV-2 (High Impact)</option>
              <option value="SEV-3">SEV-3 (Minor Warning)</option>
            </select>
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-700 mb-1">
            Incident Alert & Diagnostic Description
          </label>
          <textarea
            id="incident-desc-input"
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-2 text-slate-900 focus:ring-1 focus:ring-slate-900 focus:border-slate-900"
            placeholder="Paste telemetry alert, stack trace, or incident symptoms..."
          />
        </div>

        {/* Human Token Input for Tier-3 Tests */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 pt-2">
          <div className="flex-1">
            <div className="flex items-center gap-1.5 mb-1">
              <KeyRound className="w-3.5 h-3.5 text-amber-600" />
              <label className="text-xs font-medium text-slate-700">
                Tier-3 Human Authorization Token (Optional)
              </label>
            </div>
            <div className="flex items-center gap-2">
              <input
                id="human-token-input"
                type="text"
                value={humanToken}
                onChange={(e) => setHumanToken(e.target.value)}
                placeholder="e.g. TOKEN-HUMAN-APPROVED-88219 (required to bypass Tier-3 auth-service gate)"
                className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-1.5 text-slate-900"
              />
              <button
                id="fill-valid-token-btn"
                onClick={() => setHumanToken("TOKEN-HUMAN-APPROVED-88219")}
                className="px-2.5 py-1.5 rounded-lg border border-slate-300 text-[11px] font-mono text-slate-600 hover:bg-slate-50 whitespace-nowrap"
              >
                Insert Valid Token
              </button>
            </div>
          </div>

          <button
            id="execute-workflow-btn"
            disabled={isExecuting}
            onClick={handleRunIncident}
            className="flex items-center justify-center gap-2 px-6 py-2.5 rounded-lg bg-slate-900 text-white text-xs font-semibold hover:bg-slate-800 disabled:opacity-50 transition-colors shadow-sm self-end"
          >
            {isExecuting ? (
              <>
                <RotateCcw className="w-4 h-4 animate-spin" />
                Orchestrating...
              </>
            ) : (
              <>
                <Play className="w-4 h-4 text-emerald-400" />
                Execute Multi-Agent Workflow
              </>
            )}
          </button>
        </div>
      </div>

      {/* Execution Results & State Machine Visualization */}
      {result && (
        <div className="space-y-6 animate-in fade-in duration-300">
          {/* State Machine Transition Progress Bar */}
          <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  State Machine Lifecycle Transition
                </h3>
                <span className="font-mono text-xs text-slate-400">
                  Run ID: {result.run_id}
                </span>
              </div>
              <span
                className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold font-mono ${
                  result.final_state === "COMPLETED"
                    ? "bg-emerald-100 text-emerald-800 border border-emerald-300"
                    : result.final_state === "AWAITING_APPROVAL"
                    ? "bg-amber-100 text-amber-800 border border-amber-300"
                    : "bg-red-100 text-red-800 border border-red-300"
                }`}
              >
                {result.final_state === "COMPLETED" && <CheckCircle2 className="w-3.5 h-3.5" />}
                {result.final_state === "AWAITING_APPROVAL" && <AlertTriangle className="w-3.5 h-3.5" />}
                {result.final_state === "FAILED" && <XCircle className="w-3.5 h-3.5" />}
                FINAL STATE: {result.final_state}
              </span>
            </div>

            {/* Visual FSM Nodes */}
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
              {workflowStages.map((stage, idx) => {
                const reached = result.steps.some(
                  (s) => s.state_transition.to === stage || s.state_transition.from === stage
                );
                const isFinal = result.final_state === stage;
                const isHaltState =
                  stage === "EXECUTING" &&
                  (result.final_state === "AWAITING_APPROVAL" || result.final_state === "FAILED");

                return (
                  <div
                    key={stage}
                    className={`p-2.5 rounded-lg border text-center transition-all ${
                      isFinal
                        ? "bg-slate-900 text-white border-slate-900 ring-2 ring-slate-900"
                        : isHaltState
                        ? "bg-red-50 border-red-200 text-red-700"
                        : reached
                        ? "bg-emerald-50/70 border-emerald-200 text-emerald-900"
                        : "bg-slate-50 border-slate-200 text-slate-400"
                    }`}
                  >
                    <div className="text-[10px] font-mono opacity-70 mb-0.5">
                      0{idx + 1}
                    </div>
                    <div className="text-xs font-semibold font-mono tracking-tight truncate">
                      {stage}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Telemetry Before vs After Comparison */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
              <div className="flex items-center justify-between mb-3 border-b border-slate-100 pb-2">
                <span className="text-xs font-semibold text-slate-700">
                  Pre-Remediation Telemetry (Alert State)
                </span>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-red-50 text-red-700 border border-red-200">
                  {result.telemetry_before.status}
                </span>
              </div>
              <div className="grid grid-cols-4 gap-2 text-center">
                <div className="p-2 rounded bg-slate-50 border border-slate-100">
                  <div className="text-[10px] text-slate-500">Heap Memory</div>
                  <div className="text-sm font-bold font-mono text-red-600">
                    {result.telemetry_before.memory_utilization_pct}%
                  </div>
                </div>
                <div className="p-2 rounded bg-slate-50 border border-slate-100">
                  <div className="text-[10px] text-slate-500">CPU Load</div>
                  <div className="text-sm font-bold font-mono text-slate-800">
                    {result.telemetry_before.cpu_utilization_pct}%
                  </div>
                </div>
                <div className="p-2 rounded bg-slate-50 border border-slate-100">
                  <div className="text-[10px] text-slate-500">P99 Latency</div>
                  <div className="text-sm font-bold font-mono text-red-600">
                    {result.telemetry_before.p99_latency_ms}ms
                  </div>
                </div>
                <div className="p-2 rounded bg-slate-50 border border-slate-100">
                  <div className="text-[10px] text-slate-500">Error Rate</div>
                  <div className="text-sm font-bold font-mono text-red-600">
                    {result.telemetry_before.error_rate_pct}%
                  </div>
                </div>
              </div>
            </div>

            <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
              <div className="flex items-center justify-between mb-3 border-b border-slate-100 pb-2">
                <span className="text-xs font-semibold text-slate-700">
                  Post-Verification Telemetry
                </span>
                <span
                  className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                    result.telemetry_after?.status === "HEALTHY"
                      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                      : "bg-slate-100 text-slate-600 border-slate-200"
                  }`}
                >
                  {result.telemetry_after ? result.telemetry_after.status : "UNMODIFIED"}
                </span>
              </div>
              <div className="grid grid-cols-4 gap-2 text-center">
                <div className="p-2 rounded bg-slate-50 border border-slate-100">
                  <div className="text-[10px] text-slate-500">Heap Memory</div>
                  <div className="text-sm font-bold font-mono text-slate-800">
                    {result.telemetry_after ? `${result.telemetry_after.memory_utilization_pct}%` : "—"}
                  </div>
                </div>
                <div className="p-2 rounded bg-slate-50 border border-slate-100">
                  <div className="text-[10px] text-slate-500">CPU Load</div>
                  <div className="text-sm font-bold font-mono text-slate-800">
                    {result.telemetry_after ? `${result.telemetry_after.cpu_utilization_pct}%` : "—"}
                  </div>
                </div>
                <div className="p-2 rounded bg-slate-50 border border-slate-100">
                  <div className="text-[10px] text-slate-500">P99 Latency</div>
                  <div className="text-sm font-bold font-mono text-emerald-600">
                    {result.telemetry_after ? `${result.telemetry_after.p99_latency_ms}ms` : "—"}
                  </div>
                </div>
                <div className="p-2 rounded bg-slate-50 border border-slate-100">
                  <div className="text-[10px] text-slate-500">Error Rate</div>
                  <div className="text-sm font-bold font-mono text-emerald-600">
                    {result.telemetry_after ? `${result.telemetry_after.error_rate_pct}%` : "—"}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Interactive Multi-Agent Trace & Replay Steps */}
          <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-100 pb-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-900">
                  Structured A2A Execution Trace & Replay Steps
                </h3>
                <p className="text-xs text-slate-500">
                  Click any step to inspect typed message envelopes, decisions, and runbook citations.
                </p>
              </div>
              <div className="flex items-center gap-3 text-xs font-mono text-slate-500">
                <span>Latency: {result.total_latency_ms}ms</span>
                <span>•</span>
                <span>Tokens: {result.total_tokens}</span>
                <span>•</span>
                <span>Cost: ${result.total_cost_usd}</span>
              </div>
            </div>

            {/* Step Selection Tabs */}
            <div className="flex gap-2 overflow-x-auto no-scrollbar pb-1">
              {result.steps.map((st, idx) => {
                const isSelected = selectedStep === idx;
                return (
                  <button
                    key={st.step_number}
                    id={`step-select-btn-${idx}`}
                    onClick={() => setSelectedStep(idx)}
                    className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs font-medium whitespace-nowrap transition-colors ${
                      isSelected
                        ? "bg-slate-900 text-white border-slate-900"
                        : "bg-slate-50 text-slate-700 border-slate-200 hover:bg-slate-100"
                    }`}
                  >
                    <span className="font-mono text-[10px] opacity-75">
                      Step {st.step_number}
                    </span>
                    <span className="uppercase font-mono text-[11px]">
                      {st.agent}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* Step Detail Card */}
            {result.steps[selectedStep] && (
              <div className="border border-slate-200 rounded-lg p-4 bg-slate-50/50 space-y-3">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="px-2 py-0.5 rounded text-xs font-mono font-semibold bg-slate-900 text-white uppercase">
                      {result.steps[selectedStep].agent}
                    </span>
                    <span className="text-xs font-mono font-medium text-slate-700">
                      {result.steps[selectedStep].action}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-[11px] font-mono text-slate-500">
                    <span>
                      {result.steps[selectedStep].state_transition.from} →{" "}
                      {result.steps[selectedStep].state_transition.to}
                    </span>
                    <span>•</span>
                    <span>{result.steps[selectedStep].latency_ms}ms</span>
                    <span>•</span>
                    <span>{result.steps[selectedStep].tokens_used} tokens</span>
                  </div>
                </div>

                {/* Decisions Log */}
                <div className="bg-white border border-slate-200 rounded-md p-3">
                  <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1.5">
                    Decisions & Reasoning Log
                  </div>
                  <ul className="space-y-1">
                    {result.steps[selectedStep].decisions.map((d, dIdx) => (
                      <li key={dIdx} className="text-xs text-slate-700 flex items-start gap-2 font-sans">
                        <span className="text-emerald-500 font-bold">✓</span>
                        {d}
                      </li>
                    ))}
                  </ul>
                </div>

                {/* A2A Structured Message Envelope */}
                {result.steps[selectedStep].message && (
                  <div>
                    <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1">
                      Structured A2A Message Envelope
                    </div>
                    <pre className="text-[11px] font-mono bg-slate-900 text-slate-100 p-3 rounded-md overflow-x-auto max-h-56">
                      {JSON.stringify(result.steps[selectedStep].message, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
