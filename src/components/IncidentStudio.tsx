import React, { useEffect, useState } from "react";
import {
  Play,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  RotateCcw,
  RefreshCw,
  KeyRound,
  Activity,
  Loader2,
  Terminal,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import {
  ApiError,
  EvalScenarioSummary,
  ServiceGraphNode,
  approveIncident,
  getHealth,
  listEvalScenarios,
  listServices,
  replayTrace,
  resetSimulation,
  submitIncident,
} from "../utils/apiClient";

const WORKFLOW_STAGES = [
  "TRIAGE",
  "PLANNING",
  "INVESTIGATING",
  "PROPOSING_ACTION",
  "SAFETY_VERIFICATION",
  "EXECUTING",
  "VERIFYING_RECOVERY",
  "COMPLETED",
];

export const IncidentStudio: React.FC = () => {
  const [nodes, setNodes] = useState<ServiceGraphNode[]>([]);
  const [scenarios, setScenarios] = useState<EvalScenarioSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(null);
  const [service, setService] = useState<string>("payment-service");
  const [environment, setEnvironment] = useState<string>("prod");
  const [severity, setSeverity] = useState<"SEV-1" | "SEV-2" | "SEV-3">("SEV-1");
  const [description, setDescription] = useState<string>(
    "payment-service memory utilization at 96.2%, multiple OutOfMemoryError exceptions in logs, 503 errors on checkout gateway."
  );

  const [result, setResult] = useState<Record<string, any> | null>(null);
  const [isExecuting, setIsExecuting] = useState<boolean>(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [selectedStep, setSelectedStep] = useState<number>(0);

  const [approvalToken, setApprovalToken] = useState<string>("");
  const [isApproving, setIsApproving] = useState<boolean>(false);
  const [approveError, setApproveError] = useState<string | null>(null);

  const [replayText, setReplayText] = useState<string | null>(null);
  const [replayOpen, setReplayOpen] = useState<boolean>(false);
  const [replayLoading, setReplayLoading] = useState<boolean>(false);

  const [simulationId, setSimulationId] = useState<string | null>(null);
  const [isResettingSimulation, setIsResettingSimulation] = useState<boolean>(false);
  const [resetError, setResetError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([listServices(), listEvalScenarios()])
      .then(([servicesRes, scenariosRes]) => {
        setNodes(servicesRes.nodes);
        setScenarios(scenariosRes.scenarios);
      })
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : String(err)));
    getHealth()
      .then((health) => setSimulationId(health.simulation_id))
      .catch(() => {
        /* Surfaced already by the services/scenarios load error above. */
      });
  }, []);

  const handleResetSimulation = () => {
    setIsResettingSimulation(true);
    setResetError(null);
    resetSimulation()
      .then((res) => {
        setSimulationId(res.simulation_id);
        // Prior results reference the simulation context that just went away.
        setResult(null);
        setReplayText(null);
        setReplayOpen(false);
        setApprovalToken("");
        setApproveError(null);
      })
      .catch((err) => setResetError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setIsResettingSimulation(false));
  };

  const handleLoadScenario = (sc: EvalScenarioSummary) => {
    setSelectedScenarioId(sc.id);
    setService(sc.service);
    setEnvironment(sc.environment);
    setSeverity(sc.severity);
    setDescription(sc.description);
    setResult(null);
    setApprovalToken("");
    setReplayText(null);
  };

  const handleRunIncident = () => {
    setIsExecuting(true);
    setSubmitError(null);
    setResult(null);
    setReplayText(null);
    setReplayOpen(false);

    submitIncident({
      title: `${service} Operational Alert (${severity})`,
      description,
      service,
      severity,
      environment,
    })
      .then((res) => {
        setResult(res);
        setSelectedStep(Math.max(0, (res.trace?.steps?.length ?? 1) - 1));
      })
      .catch((err) => setSubmitError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setIsExecuting(false));
  };

  const handleApprove = () => {
    if (!result?.run_id || !approvalToken.trim()) return;
    setIsApproving(true);
    setApproveError(null);
    approveIncident(result.run_id, approvalToken.trim())
      .then((res) => {
        setResult(res);
        setSelectedStep(Math.max(0, (res.trace?.steps?.length ?? 1) - 1));
      })
      .catch((err) => setApproveError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setIsApproving(false));
  };

  const handleToggleReplay = () => {
    if (replayOpen) {
      setReplayOpen(false);
      return;
    }
    setReplayOpen(true);
    if (replayText || !result?.run_id) return;
    setReplayLoading(true);
    replayTrace(result.run_id)
      .then((res) => setReplayText(res.replay))
      .catch((err) => setReplayText(`Could not load replay: ${err instanceof ApiError ? err.message : String(err)}`))
      .finally(() => setReplayLoading(false));
  };

  const finalState: string | undefined = result?.final_state;
  const steps: any[] = result?.trace?.steps || [];
  const transitions: any[] = result?.trace?.state_transitions || [];
  const reachedStages = new Set(transitions.flatMap((t) => [t.from_state, t.to_state]));

  const investigatorMetrics = steps.find((s) => s.agent === "investigator")?.outputs_redacted
    ?.metrics_summary;
  const postRecoveryMetrics = result?.post_recovery_metrics;

  return (
    <div className="space-y-6">
      {loadError && (
        <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          {loadError}
        </div>
      )}

      {/* Scenario Presets Banner */}
      <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 sm:p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-900 tracking-tight">
              Pre-Configured Benchmark Scenarios
            </h2>
            <p className="text-xs text-slate-500">
              Live from GET /eval/scenarios. Selecting one fills the form below; it still runs
              through the real workflow when you execute it.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {scenarios.map((sc) => {
            const isSelected = selectedScenarioId === sc.id;
            return (
              <button
                key={sc.id}
                id={`scenario-preset-btn-${sc.id}`}
                onClick={() => handleLoadScenario(sc)}
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
                      sc.expected.expected_final_state === "COMPLETED"
                        ? "bg-emerald-100 text-emerald-800"
                        : sc.expected.expected_final_state === "AWAITING_APPROVAL"
                        ? "bg-amber-100 text-amber-800"
                        : sc.expected.expected_final_state
                        ? "bg-red-100 text-red-800"
                        : "bg-slate-100 text-slate-600"
                    }`}
                  >
                    {/* Invariant scenarios (idempotency, isolation) assert on a
                        rejection code rather than a terminal state. */}
                    Expected:{" "}
                    {sc.expected.expected_final_state ||
                      sc.expected.expected_rejection_code ||
                      "invariant hold"}
                  </span>
                </div>
                <div className="text-xs font-medium text-slate-900 line-clamp-1 mb-1">
                  {sc.name}
                </div>
                <div className="text-[11px] text-slate-500 line-clamp-2">{sc.description}</div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Incident Input & Controls */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
        <div className="flex items-center justify-between gap-2 border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-blue-600" />
            <h3 className="text-sm font-semibold text-slate-900">Incident Definition</h3>
          </div>
          <div className="flex items-center gap-2">
            {simulationId && (
              <span
                title="Identifies the current simulated cluster and idempotency window. Changes on reset."
                className="text-[10px] font-mono text-slate-400"
              >
                {simulationId}
              </span>
            )}
            <button
              id="reset-simulation-btn"
              type="button"
              disabled={isResettingSimulation}
              onClick={handleResetSimulation}
              title="Start a fresh simulated cluster and clear the idempotency window, without restarting the backend -- use this between independent test/demo incidents on the same service."
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-slate-200 text-[11px] font-medium text-slate-600 hover:bg-slate-50 hover:text-slate-900 disabled:opacity-50 transition-colors"
            >
              <RefreshCw className={`w-3 h-3 ${isResettingSimulation ? "animate-spin" : ""}`} />
              {isResettingSimulation ? "Resetting..." : "New Simulation"}
            </button>
          </div>
        </div>

        {resetError && (
          <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            {resetError}
          </div>
        )}

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
              {nodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.id} ({n.tier})
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Target Environment
            </label>
            <select
              id="select-env"
              value={environment}
              onChange={(e) => setEnvironment(e.target.value)}
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

        <button
          id="execute-workflow-btn"
          disabled={isExecuting}
          onClick={handleRunIncident}
          className="flex items-center justify-center gap-2 px-6 py-2.5 rounded-lg bg-slate-900 text-white text-xs font-semibold hover:bg-slate-800 disabled:opacity-50 transition-colors shadow-sm"
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

        {submitError && (
          <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            {submitError}
          </div>
        )}
      </div>

      {/* Execution Results */}
      {result && (
        <div className="space-y-6 animate-in fade-in duration-300">
          {/* State Machine Transition Progress Bar */}
          <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm">
            <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
              <div className="flex items-center gap-2">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  State Machine Lifecycle Transition
                </h3>
                <span className="font-mono text-xs text-slate-400">Run ID: {result.run_id}</span>
              </div>
              <span
                className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold font-mono ${
                  finalState === "COMPLETED"
                    ? "bg-emerald-100 text-emerald-800 border border-emerald-300"
                    : finalState === "AWAITING_APPROVAL"
                    ? "bg-amber-100 text-amber-800 border border-amber-300"
                    : "bg-red-100 text-red-800 border border-red-300"
                }`}
              >
                {finalState === "COMPLETED" && <CheckCircle2 className="w-3.5 h-3.5" />}
                {finalState === "AWAITING_APPROVAL" && <AlertTriangle className="w-3.5 h-3.5" />}
                {finalState !== "COMPLETED" && finalState !== "AWAITING_APPROVAL" && (
                  <XCircle className="w-3.5 h-3.5" />
                )}
                FINAL STATE: {finalState}
              </span>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2">
              {WORKFLOW_STAGES.map((stage, idx) => {
                const reached = reachedStages.has(stage);
                const isFinalStage = finalState === stage;
                return (
                  <div
                    key={stage}
                    className={`p-2.5 rounded-lg border text-center transition-all ${
                      isFinalStage
                        ? "bg-slate-900 text-white border-slate-900 ring-2 ring-slate-900"
                        : reached
                        ? "bg-emerald-50/70 border-emerald-200 text-emerald-900"
                        : "bg-slate-50 border-slate-200 text-slate-400"
                    }`}
                  >
                    <div className="text-[10px] font-mono opacity-70 mb-0.5">0{idx + 1}</div>
                    <div className="text-xs font-semibold font-mono tracking-tight truncate">
                      {stage}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Human approval callout, only while genuinely held */}
          {result.status === "BLOCKED_FOR_APPROVAL" && result.proposal && (
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-5 space-y-3">
              <div className="flex items-center gap-2">
                <KeyRound className="w-4 h-4 text-amber-700" />
                <h3 className="text-sm font-semibold text-amber-900">
                  Held for Tier-3 Human Approval
                </h3>
              </div>
              <p className="text-xs text-amber-800">{result.reason}</p>

              <div className="bg-slate-900 text-slate-100 rounded-lg p-3 font-mono text-[11px] flex items-start gap-2">
                <Terminal className="w-3.5 h-3.5 mt-0.5 shrink-0 text-emerald-400" />
                <span>
                  mini_platform cli approve --run-id {result.run_id} --approver &lt;your-name&gt;
                </span>
              </div>
              <p className="text-[11px] text-amber-700">
                This mints a token signed against exactly this proposal ({result.proposal.tool_name}{" "}
                on {result.proposal.service}) from a host holding the signing secret -- never from
                this browser.
              </p>

              <div className="flex items-center gap-2 pt-1">
                <input
                  type="text"
                  value={approvalToken}
                  onChange={(e) => setApprovalToken(e.target.value)}
                  placeholder="Or paste an already-minted token to approve now"
                  className="flex-1 text-xs font-mono rounded-lg border-amber-300 border px-3 py-1.5 text-slate-900"
                />
                <button
                  onClick={handleApprove}
                  disabled={isApproving || !approvalToken.trim()}
                  className="flex items-center gap-2 px-4 py-1.5 rounded-lg bg-amber-900 text-white text-xs font-semibold hover:bg-amber-800 disabled:opacity-50"
                >
                  {isApproving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : "Approve"}
                </button>
              </div>
              {approveError && (
                <div className="flex items-center gap-2 text-xs text-red-700">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                  {approveError}
                </div>
              )}
            </div>
          )}

          {/* Proposal & Safety */}
          {result.proposal && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm space-y-2">
                <div className="text-xs font-semibold text-slate-700 border-b border-slate-100 pb-2">
                  Action Proposal
                </div>
                <div className="text-xs font-mono text-slate-900">
                  <strong>{result.proposal.tool_name}</strong> on {result.proposal.service}
                </div>
                <p className="text-xs text-slate-600">{result.proposal.reasoning}</p>
                <pre className="text-[11px] font-mono bg-slate-50 border border-slate-200 rounded p-2 overflow-x-auto">
                  {JSON.stringify(result.proposal.parameters, null, 2)}
                </pre>
              </div>

              <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm space-y-2">
                <div className="flex items-center justify-between border-b border-slate-100 pb-2">
                  <span className="text-xs font-semibold text-slate-700">Safety Verdict</span>
                  {result.safety_result && (
                    <span
                      className={`text-[11px] font-mono font-bold px-2 py-0.5 rounded border ${
                        result.safety_result.approved
                          ? "bg-emerald-100 text-emerald-800 border-emerald-300"
                          : "bg-amber-100 text-amber-800 border-amber-300"
                      }`}
                    >
                      Tier {result.safety_result.tier}
                    </span>
                  )}
                </div>
                {result.safety_result && (
                  <>
                    <div className="text-xs text-slate-700 font-mono">
                      Blast radius: {result.safety_result.blast_radius_analysis?.total_affected_services}{" "}
                      services ({result.safety_result.blast_radius_analysis?.risk_rating})
                    </div>
                    {result.execution_result && (
                      <div
                        className={`text-xs font-mono px-2 py-1 rounded ${
                          result.execution_result.ok
                            ? "bg-emerald-50 text-emerald-800"
                            : "bg-red-50 text-red-800"
                        }`}
                      >
                        {result.execution_result.ok
                          ? result.execution_result.data?.message
                          : result.execution_result.error?.message}
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          )}

          {/* Telemetry Before vs After */}
          {(investigatorMetrics || postRecoveryMetrics) && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
                <div className="flex items-center justify-between mb-3 border-b border-slate-100 pb-2">
                  <span className="text-xs font-semibold text-slate-700">
                    Telemetry at Investigation Time
                  </span>
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-red-50 text-red-700 border border-red-200">
                    {investigatorMetrics?.status ?? "—"}
                  </span>
                </div>
                <TelemetryGrid metrics={investigatorMetrics} />
              </div>

              <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
                <div className="flex items-center justify-between mb-3 border-b border-slate-100 pb-2">
                  <span className="text-xs font-semibold text-slate-700">
                    Post-Remediation Telemetry
                  </span>
                  <span
                    className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                      postRecoveryMetrics?.status === "HEALTHY"
                        ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                        : "bg-slate-100 text-slate-600 border-slate-200"
                    }`}
                  >
                    {postRecoveryMetrics ? postRecoveryMetrics.status : "N/A"}
                  </span>
                </div>
                <TelemetryGrid metrics={postRecoveryMetrics} healthy />
              </div>
            </div>
          )}

          {/* Agent Execution Trace */}
          {steps.length > 0 && (
            <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-100 pb-3">
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">
                    Structured A2A Execution Trace
                  </h3>
                  <p className="text-xs text-slate-500">
                    Live from the audit trace recorded by this run.
                  </p>
                </div>
                <div className="flex items-center gap-3 text-xs font-mono text-slate-500">
                  <span>Latency: {result.trace.total_latency_ms}ms</span>
                  <span>•</span>
                  <span>Tokens: {result.trace.total_tokens}</span>
                  <span>•</span>
                  <span>Cost: ${result.trace.total_cost_usd}</span>
                </div>
              </div>

              <div className="flex gap-2 overflow-x-auto no-scrollbar pb-1">
                {steps.map((st, idx) => {
                  const isSelected = selectedStep === idx;
                  return (
                    <button
                      key={st.step_id}
                      onClick={() => setSelectedStep(idx)}
                      className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs font-medium whitespace-nowrap transition-colors ${
                        isSelected
                          ? "bg-slate-900 text-white border-slate-900"
                          : "bg-slate-50 text-slate-700 border-slate-200 hover:bg-slate-100"
                      }`}
                    >
                      <span className="font-mono text-[10px] opacity-75">{idx + 1}</span>
                      <span className="uppercase font-mono text-[11px]">{st.agent}</span>
                    </button>
                  );
                })}
              </div>

              {steps[selectedStep] && (
                <div className="border border-slate-200 rounded-lg p-4 bg-slate-50/50 space-y-3">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="px-2 py-0.5 rounded text-xs font-mono font-semibold bg-slate-900 text-white uppercase">
                        {steps[selectedStep].agent}
                      </span>
                      <span className="text-xs font-mono font-medium text-slate-700">
                        {steps[selectedStep].action}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 text-[11px] font-mono text-slate-500">
                      <span>{steps[selectedStep].workflow_state}</span>
                      <span>•</span>
                      <span>{steps[selectedStep].latency_ms}ms</span>
                      <span>•</span>
                      <span>{steps[selectedStep].estimated_tokens} tokens</span>
                    </div>
                  </div>

                  <div className="bg-white border border-slate-200 rounded-md p-3">
                    <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1.5">
                      Decisions & Reasoning
                    </div>
                    <ul className="space-y-1">
                      {(steps[selectedStep].decisions || []).map((d: string, i: number) => (
                        <li key={i} className="text-xs text-slate-700 flex items-start gap-2">
                          <span className="text-emerald-500 font-bold">✓</span>
                          {d}
                        </li>
                      ))}
                      {(steps[selectedStep].rejected_alternatives || []).map(
                        (d: string, i: number) => (
                          <li key={`r${i}`} className="text-xs text-slate-500 flex items-start gap-2">
                            <span className="text-red-400 font-bold">✗</span>
                            {d}
                          </li>
                        )
                      )}
                    </ul>
                  </div>

                  <div>
                    <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1">
                      Redacted Inputs / Outputs
                    </div>
                    <pre className="text-[11px] font-mono bg-slate-900 text-slate-100 p-3 rounded-md overflow-x-auto max-h-56">
                      {JSON.stringify(
                        {
                          inputs: steps[selectedStep].inputs_redacted,
                          outputs: steps[selectedStep].outputs_redacted,
                        },
                        null,
                        2
                      )}
                    </pre>
                  </div>
                </div>
              )}

              <div className="pt-1">
                <button
                  onClick={handleToggleReplay}
                  className="flex items-center gap-1.5 text-xs font-semibold text-slate-600 hover:text-slate-900"
                >
                  {replayOpen ? (
                    <ChevronDown className="w-3.5 h-3.5" />
                  ) : (
                    <ChevronRight className="w-3.5 h-3.5" />
                  )}
                  Full post-mortem replay (GET /traces/&#123;run_id&#125;/replay)
                </button>
                {replayOpen && (
                  <pre className="mt-2 text-[11px] font-mono bg-slate-900 text-slate-100 p-3 rounded-md overflow-x-auto max-h-80 whitespace-pre-wrap">
                    {replayLoading ? "Loading replay..." : replayText}
                  </pre>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const TelemetryGrid: React.FC<{ metrics: Record<string, any> | undefined; healthy?: boolean }> = ({
  metrics,
  healthy,
}) => {
  const emphasisColor = healthy ? "text-emerald-600" : "text-red-600";
  const cell = (label: string, value: any) => (
    <div className="p-2 rounded bg-slate-50 border border-slate-100">
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className={`text-sm font-bold font-mono ${value !== undefined ? emphasisColor : "text-slate-400"}`}>
        {value !== undefined ? value : "—"}
      </div>
    </div>
  );
  return (
    <div className="grid grid-cols-4 gap-2 text-center">
      {cell("Memory", metrics ? `${metrics.memory_utilization_pct}%` : undefined)}
      {cell("CPU", metrics ? `${metrics.cpu_utilization_pct}%` : undefined)}
      {cell("P99 Latency", metrics ? `${metrics.p99_latency_ms}ms` : undefined)}
      {cell("Error Rate", metrics ? `${metrics.error_rate_pct}%` : undefined)}
    </div>
  );
};
