import React, { useEffect, useState } from "react";
import { Terminal, Play, FileJson, Loader2, AlertTriangle, Lock } from "lucide-react";
import {
  ApiError,
  McpToolDefinition,
  ServiceGraphNode,
  invokeReadOnlyTool,
  listServices,
  listTools,
} from "../utils/apiClient";

export const McpToolsView: React.FC = () => {
  const [tools, setTools] = useState<McpToolDefinition[] | null>(null);
  const [nodes, setNodes] = useState<ServiceGraphNode[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [selectedToolIdx, setSelectedToolIdx] = useState<number>(0);
  const [toolService, setToolService] = useState<string>("payment-service");
  const [output, setOutput] = useState<any>(null);
  const [invoking, setInvoking] = useState<boolean>(false);
  const [invokeError, setInvokeError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([listTools(), listServices()])
      .then(([toolsRes, servicesRes]) => {
        setTools(toolsRes.tools);
        setNodes(servicesRes.nodes);
      })
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : String(err)));
  }, []);

  const tool = tools?.[selectedToolIdx];

  const handleInvoke = () => {
    if (!tool) return;
    setInvoking(true);
    setInvokeError(null);
    setOutput(null);
    invokeReadOnlyTool(tool.name, toolService)
      .then(setOutput)
      .catch((err) => setInvokeError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setInvoking(false));
  };

  if (loadError) {
    return (
      <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-4">
        <AlertTriangle className="w-4 h-4 shrink-0" />
        {loadError}
      </div>
    );
  }

  if (!tools || !tool) {
    return (
      <div className="flex items-center gap-2 text-xs text-slate-500 p-6">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading tool catalog from GET /tools...
      </div>
    );
  }

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
          Live catalog from GET /tools. Read-only tools dispatch for real through the Tool
          Gateway; mutating tools only ever execute inside a full incident workflow.
        </p>
      </div>

      {/* Main Tool Layout */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Tool List Sidebar */}
        <div className="space-y-2">
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wide px-1">
            Registered MCP Tools ({tools.length})
          </div>
          {tools.map((t, idx) => {
            const isSelected = selectedToolIdx === idx;
            return (
              <button
                key={t.name}
                id={`mcp-tool-tab-${t.name}`}
                onClick={() => {
                  setSelectedToolIdx(idx);
                  setOutput(null);
                  setInvokeError(null);
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
                      t.mutating
                        ? isSelected
                          ? "bg-red-900/60 text-red-200 border border-red-700"
                          : "bg-red-50 text-red-700 border border-red-200"
                        : isSelected
                        ? "bg-emerald-900/60 text-emerald-200 border border-emerald-700"
                        : "bg-emerald-50 text-emerald-700 border border-emerald-200"
                    }`}
                  >
                    {t.mutating ? "MUTATING" : "READ-ONLY"}
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

        {/* Selected Tool Contract & Execution */}
        <div className="md:col-span-2 space-y-4">
          <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-100 pb-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold font-mono text-slate-900">{tool.name}</span>
                  <span className="text-xs font-mono text-slate-400">v{tool.version}</span>
                </div>
                <p className="text-xs text-slate-600 mt-0.5">{tool.description}</p>
              </div>

              <span
                className={`text-xs font-mono font-semibold px-2.5 py-1 rounded border self-start sm:self-auto ${
                  tool.mutating
                    ? "bg-amber-50 text-amber-800 border-amber-300"
                    : "bg-emerald-50 text-emerald-800 border-emerald-300"
                }`}
              >
                {tool.mutating ? "Tier 2 / Tier 3 Guarded" : "Tier 1 Permitted"}
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

            {/* Execution */}
            <div className="border-t border-slate-100 pt-4 space-y-3">
              <div className="text-xs font-semibold text-slate-800">
                {tool.mutating ? "Direct Invocation Disabled" : "Live Invocation"}
              </div>

              {tool.mutating ? (
                <div className="flex items-start gap-2 text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded-lg p-3">
                  <Lock className="w-3.5 h-3.5 mt-0.5 shrink-0 text-slate-400" />
                  <span>
                    Mutating tools only dispatch through the full Planner → Investigator → Ops →
                    Verifier workflow, never from a direct call. Submit an incident in{" "}
                    <strong>Incident Studio</strong> targeting a service to see this tool proposed
                    and adjudicated for real.
                  </span>
                </div>
              ) : (
                <>
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
                        {nodes.map((n) => (
                          <option key={n.id} value={n.id}>
                            {n.id}
                          </option>
                        ))}
                      </select>
                    </div>

                    <button
                      id="test-tool-invoke-btn"
                      onClick={handleInvoke}
                      disabled={invoking}
                      className="flex items-center justify-center gap-2 px-5 py-2 rounded-lg bg-slate-900 text-white text-xs font-semibold hover:bg-slate-800 transition-colors shadow-sm self-end disabled:opacity-50"
                    >
                      {invoking ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Play className="w-3.5 h-3.5 text-emerald-400" />
                      )}
                      Invoke Tool
                    </button>
                  </div>

                  {invokeError && (
                    <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
                      <AlertTriangle className="w-4 h-4 shrink-0" />
                      {invokeError}
                    </div>
                  )}

                  {output && (
                    <div className="space-y-1.5 pt-2">
                      <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide">
                        Live Response Envelope
                      </div>
                      <pre className="text-[11px] font-mono bg-slate-50 border border-slate-200 text-slate-800 p-3 rounded-lg overflow-x-auto max-h-96 overflow-y-auto">
                        {JSON.stringify(output, null, 2)}
                      </pre>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
