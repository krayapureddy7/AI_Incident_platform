import React, { useEffect, useState } from "react";
import { CheckCircle2, XCircle, Play, RotateCcw, ShieldCheck, AlertTriangle } from "lucide-react";
import {
  ApiError,
  EvalRunResponse,
  EvalScenarioSummary,
  listEvalScenarios,
  runEvaluationSuite,
} from "../utils/apiClient";

export const EvaluationSuiteView: React.FC = () => {
  const [scenarios, setScenarios] = useState<EvalScenarioSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [runResult, setRunResult] = useState<EvalRunResponse | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  useEffect(() => {
    listEvalScenarios()
      .then((res) => setScenarios(res.scenarios))
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : String(err)));
  }, []);

  const handleRunEvaluation = () => {
    setIsRunning(true);
    setRunError(null);
    runEvaluationSuite()
      .then(setRunResult)
      .catch((err) => setRunError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setIsRunning(false));
  };

  const total = runResult ? runResult.total : scenarios?.length ?? 0;
  const passedCount = runResult ? runResult.passed : 0;
  const failedCount = runResult ? runResult.failed : 0;
  const passRate = runResult ? runResult.pass_rate_pct.toFixed(1) : "--";

  return (
    <div className="space-y-6">
      {/* Header & Controls */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <ShieldCheck className="w-5 h-5 text-emerald-600" />
              <h2 className="text-base font-semibold text-slate-900">
                Continuous Evaluation & Invariant Suite
              </h2>
            </div>
            <p className="text-xs text-slate-500">
              Live from POST /eval/run -- each scenario builds its own isolated orchestrator and
              mock cluster, so running this never touches real incident data.
            </p>
          </div>

          <button
            id="run-evals-btn"
            disabled={isRunning}
            onClick={handleRunEvaluation}
            className="flex items-center justify-center gap-2 px-5 py-2.5 rounded-lg bg-slate-900 text-white text-xs font-semibold hover:bg-slate-800 disabled:opacity-50 transition-colors shadow-sm whitespace-nowrap"
          >
            {isRunning ? (
              <>
                <RotateCcw className="w-4 h-4 animate-spin" />
                Executing Suite...
              </>
            ) : (
              <>
                <Play className="w-4 h-4 text-emerald-400" />
                Run Invariant Evaluation Gate
              </>
            )}
          </button>
        </div>

        {runError && (
          <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-3 mt-4">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            {runError}
          </div>
        )}

        {/* Stats Row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-5 pt-4 border-t border-slate-100">
          <div className="p-3 rounded-lg bg-slate-50 border border-slate-200">
            <div className="text-[11px] font-medium text-slate-500">Total Scenarios</div>
            <div className="text-xl font-bold font-mono text-slate-900">{total}</div>
          </div>
          <div className="p-3 rounded-lg bg-emerald-50 border border-emerald-200">
            <div className="text-[11px] font-medium text-emerald-700">Passed Invariants</div>
            <div className="text-xl font-bold font-mono text-emerald-800">{passedCount}</div>
          </div>
          <div className="p-3 rounded-lg bg-red-50 border border-red-200">
            <div className="text-[11px] font-medium text-red-700">Failed Invariants</div>
            <div className="text-xl font-bold font-mono text-red-800">{failedCount}</div>
          </div>
          <div className="p-3 rounded-lg bg-slate-50 border border-slate-200">
            <div className="text-[11px] font-medium text-slate-500">Pass Rate</div>
            <div className="text-xl font-bold font-mono text-emerald-600">{passRate}%</div>
          </div>
        </div>
      </div>

      {loadError && (
        <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-4">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          {loadError}
        </div>
      )}

      {/* Scenario Breakdown Cards */}
      <div className="space-y-4">
        {(runResult ? runResult.results : scenarios || []).map((res) => {
          const isRun = "checks" in (res as any) || "details" in (res as any);
          const passed = "passed" in res ? (res as any).passed : null;
          const id = "scenario_id" in res ? (res as any).scenario_id : (res as any).id;
          const name = res.name;
          const finalState = "final_state" in res ? (res as any).final_state : undefined;
          const details: string[] = "details" in res ? (res as any).details : [];

          return (
            <div
              key={id}
              className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-3"
            >
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-100 pb-3">
                <div className="flex items-center gap-3">
                  {!isRun ? (
                    <div className="w-7 h-7 rounded-full bg-slate-100 flex items-center justify-center text-slate-400">
                      <ShieldCheck className="w-4 h-4" />
                    </div>
                  ) : passed ? (
                    <div className="w-7 h-7 rounded-full bg-emerald-100 flex items-center justify-center text-emerald-700">
                      <CheckCircle2 className="w-4 h-4" />
                    </div>
                  ) : (
                    <div className="w-7 h-7 rounded-full bg-red-100 flex items-center justify-center text-red-700">
                      <XCircle className="w-4 h-4" />
                    </div>
                  )}
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs font-semibold px-2 py-0.5 rounded bg-slate-100 text-slate-800">
                        {id}
                      </span>
                      <span className="text-xs font-semibold text-slate-900">{name}</span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  {finalState && (
                    <span className="text-xs font-mono text-slate-500">
                      State: <strong className="text-slate-900">{finalState}</strong>
                    </span>
                  )}
                  {isRun ? (
                    <span
                      className={`text-xs font-mono font-bold px-2 py-0.5 rounded border ${
                        passed
                          ? "text-emerald-700 bg-emerald-50 border-emerald-200"
                          : "text-red-700 bg-red-50 border-red-200"
                      }`}
                    >
                      {passed ? "PASSED" : "FAILED"}
                    </span>
                  ) : (
                    <span className="text-xs font-mono text-slate-400 font-bold px-2 py-0.5 rounded bg-slate-50 border border-slate-200">
                      NOT YET RUN
                    </span>
                  )}
                </div>
              </div>

              {/* Individual Invariant Assertions */}
              {isRun ? (
                <div className="space-y-2 pt-1">
                  <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                    Verified Invariant Assertions
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                    {details.map((d, i) => {
                      const isPass = d.startsWith("PASS:");
                      return (
                        <div
                          key={i}
                          className="flex items-start gap-2.5 p-2.5 rounded-lg border border-slate-100 bg-slate-50/60"
                        >
                          <span
                            className={`mt-0.5 font-bold ${
                              isPass ? "text-emerald-600" : "text-red-600"
                            }`}
                          >
                            {isPass ? "✓" : "✗"}
                          </span>
                          <div className="text-[11px] font-mono text-slate-600">
                            {d.replace(/^(PASS|FAIL):\s*/, "")}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : (
                <div className="text-xs text-slate-500 pt-1">
                  Target: <span className="font-mono">{(res as any).service}</span> in{" "}
                  <span className="font-mono">{(res as any).environment}</span> -- expects final
                  state{" "}
                  <span className="font-mono font-semibold text-slate-800">
                    {(res as any).expected?.expected_final_state}
                  </span>
                  . Run the suite above to execute it.
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
