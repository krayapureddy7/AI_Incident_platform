import React, { useState } from "react";
import {
  Terminal,
  Code,
  CheckCircle,
  Play,
  Server,
  Layers,
  FileJson,
} from "lucide-react";
import { MCP_TOOLS_REGISTRY, SERVICES_GRAPH } from "../data/platformData";

export const McpToolsView: React.FC = () => {
  const [selectedToolIdx, setSelectedToolIdx] = useState<number>(0);
  const [toolService, setToolService] = useState<string>("payment-service");
  const [mockOutput, setMockOutput] = useState<any>(null);

  const tool = MCP_TOOLS_REGISTRY[selectedToolIdx];

  const handleExecuteMockTool = () => {
    if (tool.name === "get_logs") {
      setMockOutput({
        status: "SUCCESS",
        tool: "get_logs",
        service: toolService,
        timeframe: "15m",
        total_lines: 48,
        log_sample: [
          `2026-09-03T09:00:12Z [WARN] ${toolService}: Connection pool queue depth > 40`,
          `2026-09-03T09:01:05Z [ERROR] ${toolService}: java.lang.OutOfMemoryError: Java heap space`,
          `2026-09-03T09:01:06Z [ERROR] ${toolService}: Kubelet: Container oom-killed exit status 137`,
          `2026-09-03T09:02:10Z [WARN] ${toolService}: p99 latency spiked to 4820ms`,
        ],
      });
    } else if (tool.name === "get_metrics") {
      const isAuth = toolService === "auth-service";
      setMockOutput({
        status: "SUCCESS",
        tool: "get_metrics",
        service: toolService,
        metrics: {
          memory_utilization_pct: isAuth ? 74.2 : 96.2,
          cpu_utilization_pct: isAuth ? 94.0 : 88.5,
          p99_latency_ms: isAuth ? 3200 : 4820,
          error_rate_pct: isAuth ? 14.8 : 19.4,
          active_replicas: isAuth ? 4 : 3,
        },
      });
    } else if (tool.name === "get_dependency_graph") {
      const node = SERVICES_GRAPH[toolService];
      setMockOutput({
        status: "SUCCESS",
        tool: "get_dependency_graph",
        service: toolService,
        tier: node?.tier || "tier-1",
        upstream_dependencies: node?.dependencies || [],
        downstream_dependents: node?.dependents || [],
        owner: node?.owner || "unknown",
      });
    } else if (tool.name === "simulate_restart") {
      setMockOutput({
        status: "SUCCESS",
        tool: "simulate_restart",
        service: toolService,
        action: "ROLLING_POD_RESTART",
        pods_restarted: 3,
        health_check: "PASSED",
        message: `Successfully executed rolling restart on ${toolService}. Heap buffers cleared.`,
      });
    } else if (tool.name === "simulate_scale") {
      setMockOutput({
        status: "SUCCESS",
        tool: "simulate_scale",
        service: toolService,
        action: "SCALE_REPLICAS",
        previous_replicas: 3,
        new_replicas: 5,
        hpa_status: "STABILIZED",
      });
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm">
        <div className="flex items-center gap-2 mb-1">
          <Terminal className="w-5 h-5 text-slate-900" />
          <h2 className="text-base font-semibold text-slate-900">
            Model Context Protocol (MCP) Tool Server Registry
          </h2>
        </div>
        <p className="text-xs text-slate-500">
          Tools follow strict JSON Schema contracts with environment scoping, parameter validation, and standard error envelopes.
        </p>
      </div>

      {/* Main Tool Layout */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Tool List Sidebar */}
        <div className="space-y-2">
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wide px-1">
            Registered MCP Tools ({MCP_TOOLS_REGISTRY.length})
          </div>
          {MCP_TOOLS_REGISTRY.map((t, idx) => {
            const isSelected = selectedToolIdx === idx;
            return (
              <button
                key={t.name}
                id={`mcp-tool-tab-${t.name}`}
                onClick={() => {
                  setSelectedToolIdx(idx);
                  setMockOutput(null);
                }}
                className={`w-full text-left p-3 rounded-lg border transition-all ${
                  isSelected
                    ? "bg-slate-900 text-white border-slate-900 shadow-sm"
                    : "bg-white border-slate-200 text-slate-800 hover:bg-slate-50"
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-mono text-xs font-bold">{t.name}</span>
                  <span
                    className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                      t.is_mutating
                        ? isSelected
                          ? "bg-red-900/60 text-red-200 border border-red-700"
                          : "bg-red-50 text-red-700 border border-red-200"
                        : isSelected
                        ? "bg-emerald-900/60 text-emerald-200 border border-emerald-700"
                        : "bg-emerald-50 text-emerald-700 border border-emerald-200"
                    }`}
                  >
                    {t.is_mutating ? "MUTATING" : "READ-ONLY"}
                  </span>
                </div>
                <div
                  className={`text-[11px] line-clamp-2 ${
                    isSelected ? "text-slate-300" : "text-slate-500"
                  }`}
                >
                  {t.description}
                </div>
              </button>
            );
          })}
        </div>

        {/* Selected Tool Contract & Mock Execution */}
        <div className="md:col-span-2 space-y-4">
          <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-100 pb-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold font-mono text-slate-900">
                    {tool.name}
                  </span>
                  <span className="text-xs font-mono text-slate-400">
                    v{tool.version}
                  </span>
                </div>
                <p className="text-xs text-slate-600 mt-0.5">{tool.description}</p>
              </div>

              <span
                className={`text-xs font-mono font-semibold px-2.5 py-1 rounded border self-start sm:self-auto ${
                  tool.is_mutating
                    ? "bg-amber-50 text-amber-800 border-amber-300"
                    : "bg-emerald-50 text-emerald-800 border-emerald-300"
                }`}
              >
                {tool.is_mutating ? "Tier 2 / Tier 3 Guarded" : "Tier 1 Permitted"}
              </span>
            </div>

            {/* JSON Schema Definition */}
            <div>
              <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1.5 flex items-center gap-1.5">
                <FileJson className="w-3.5 h-3.5" />
                MCP JSON Schema Contract
              </div>
              <pre className="text-[11px] font-mono bg-slate-900 text-slate-100 p-3.5 rounded-lg overflow-x-auto">
                {JSON.stringify(tool.parameters, null, 2)}
              </pre>
            </div>

            {/* Test Execution */}
            <div className="border-t border-slate-100 pt-4 space-y-3">
              <div className="text-xs font-semibold text-slate-800">
                Execute Test Mock Invocation
              </div>

              <div className="flex items-center gap-3">
                <div className="flex-1">
                  <label className="block text-[11px] font-medium text-slate-600 mb-1">
                    Target Service Parameter
                  </label>
                  <select
                    value={toolService}
                    onChange={(e) => setToolService(e.target.value)}
                    className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-1.5 bg-white text-slate-900"
                  >
                    {Object.keys(SERVICES_GRAPH).map((svc) => (
                      <option key={svc} value={svc}>
                        {svc}
                      </option>
                    ))}
                  </select>
                </div>

                <button
                  id="test-tool-invoke-btn"
                  onClick={handleExecuteMockTool}
                  className="flex items-center justify-center gap-2 px-5 py-2 rounded-lg bg-slate-900 text-white text-xs font-semibold hover:bg-slate-800 transition-colors shadow-sm self-end"
                >
                  <Play className="w-3.5 h-3.5 text-emerald-400" />
                  Invoke Tool Contract
                </button>
              </div>

              {/* Output Envelope */}
              {mockOutput && (
                <div className="space-y-1.5 pt-2">
                  <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide">
                    Standard Response Envelope
                  </div>
                  <pre className="text-[11px] font-mono bg-slate-50 border border-slate-200 text-slate-800 p-3 rounded-lg overflow-x-auto">
                    {JSON.stringify(mockOutput, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
