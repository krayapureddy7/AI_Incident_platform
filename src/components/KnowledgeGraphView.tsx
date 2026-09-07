import React, { useEffect, useState } from "react";
import { Network, Search, BookOpen, Loader2, AlertTriangle } from "lucide-react";
import {
  ApiError,
  BlastRadiusAnalysis,
  KnowledgeSearchHit,
  ServiceGraphNode,
  getBlastRadius,
  listServices,
  searchKnowledge,
} from "../utils/apiClient";

export const KnowledgeGraphView: React.FC = () => {
  const [nodes, setNodes] = useState<ServiceGraphNode[] | null>(null);
  const [topologyError, setTopologyError] = useState<string | null>(null);

  const [selectedService, setSelectedService] = useState<string>("payment-service");
  const [blast, setBlast] = useState<BlastRadiusAnalysis | null>(null);
  const [blastLoading, setBlastLoading] = useState<boolean>(false);
  const [blastError, setBlastError] = useState<string | null>(null);

  const [searchQuery, setSearchQuery] = useState<string>("payment heap OOM memory leak");
  const [filterService, setFilterService] = useState<string>("");
  const [ragResults, setRagResults] = useState<KnowledgeSearchHit[] | null>(null);
  const [ragLoading, setRagLoading] = useState<boolean>(false);
  const [ragError, setRagError] = useState<string | null>(null);

  // Live service topology, fetched once.
  useEffect(() => {
    listServices()
      .then((res) => setNodes(res.nodes))
      .catch((err) => setTopologyError(err instanceof ApiError ? err.message : String(err)));
  }, []);

  // Live blast-radius, re-fetched whenever the selected service changes.
  useEffect(() => {
    setBlastLoading(true);
    setBlastError(null);
    getBlastRadius(selectedService)
      .then(setBlast)
      .catch((err) => setBlastError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setBlastLoading(false));
  }, [selectedService]);

  // Live Hybrid RAG search, debounced against keystrokes.
  useEffect(() => {
    if (!searchQuery.trim()) {
      setRagResults([]);
      return;
    }
    const handle = setTimeout(() => {
      setRagLoading(true);
      setRagError(null);
      searchKnowledge(searchQuery, { service: filterService || undefined, topK: 4 })
        .then((res) => setRagResults(res.results))
        .catch((err) => setRagError(err instanceof ApiError ? err.message : String(err)))
        .finally(() => setRagLoading(false));
    }, 300);
    return () => clearTimeout(handle);
  }, [searchQuery, filterService]);

  return (
    <div className="space-y-6">
      {/* Topology & Blast Radius Explorer */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <Network className="w-5 h-5 text-indigo-600" />
            <h2 className="text-sm font-semibold text-slate-900">
              Knowledge Graph & BFS Blast-Radius Traversal
            </h2>
          </div>
          <span className="text-xs font-mono text-slate-400">
            Live from GET /services/&#123;service&#125;/blast-radius
          </span>
        </div>

        {topologyError && (
          <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            {topologyError}
          </div>
        )}

        {/* Service Selector Chips */}
        <div>
          <label className="block text-xs font-medium text-slate-700 mb-2">
            Select Root Target Service to Calculate Downstream Impact
          </label>
          <div className="flex flex-wrap gap-2">
            {(nodes || []).map((node) => {
              const isSelected = selectedService === node.id;
              return (
                <button
                  key={node.id}
                  id={`select-graph-service-${node.id}`}
                  onClick={() => setSelectedService(node.id)}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium transition-all ${
                    isSelected
                      ? "bg-slate-900 text-white border-slate-900 shadow-sm"
                      : "bg-slate-50 text-slate-700 border-slate-200 hover:bg-slate-100"
                  }`}
                >
                  <span
                    className={`w-2 h-2 rounded-full ${
                      node.tier === "tier-0"
                        ? "bg-red-400"
                        : node.tier === "tier-1"
                        ? "bg-amber-400"
                        : "bg-emerald-400"
                    }`}
                  />
                  <span>{node.id}</span>
                  <span className="text-[10px] opacity-75 font-mono">({node.tier})</span>
                </button>
              );
            })}
            {nodes === null && !topologyError && (
              <span className="text-xs text-slate-400 flex items-center gap-1.5">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading topology...
              </span>
            )}
          </div>
        </div>

        {/* Blast Radius Analysis Card */}
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-4 space-y-4 min-h-[140px]">
          {blastLoading && (
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Computing blast radius...
            </div>
          )}
          {blastError && (
            <div className="flex items-center gap-2 text-xs text-red-700">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              {blastError}
            </div>
          )}
          {blast && !blastLoading && !blastError && (
            <>
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div>
                  <div className="text-xs font-semibold text-slate-900 flex items-center gap-2">
                    <span>
                      Target: <strong>{blast.target_service}</strong>
                    </span>
                    <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-white border border-slate-200 text-slate-700">
                      {blast.service_tier}
                    </span>
                    <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-white border border-slate-200 text-slate-600">
                      Owner: {blast.owner}
                    </span>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-slate-600">Calculated Risk:</span>
                  <span
                    className={`text-xs font-bold font-mono px-2.5 py-1 rounded border ${
                      blast.risk_rating === "CRITICAL"
                        ? "bg-red-100 text-red-800 border-red-300"
                        : blast.risk_rating === "HIGH"
                        ? "bg-amber-100 text-amber-800 border-amber-300"
                        : "bg-emerald-100 text-emerald-800 border-emerald-300"
                    }`}
                  >
                    {blast.risk_rating} RISK
                  </span>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div className="p-3 rounded-lg bg-white border border-slate-200">
                  <div className="text-[11px] font-semibold text-slate-500 uppercase">
                    Direct Dependents ({blast.direct_dependents.length})
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {blast.direct_dependents.length > 0 ? (
                      blast.direct_dependents.map((d) => (
                        <span
                          key={d}
                          className="text-xs font-mono px-2 py-0.5 rounded bg-slate-100 text-slate-800"
                        >
                          {d}
                        </span>
                      ))
                    ) : (
                      <span className="text-xs text-slate-400 italic">None (Leaf node)</span>
                    )}
                  </div>
                </div>

                <div className="p-3 rounded-lg bg-white border border-slate-200">
                  <div className="text-[11px] font-semibold text-slate-500 uppercase">
                    Transitive Dependents ({blast.transitive_dependents.length})
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {blast.transitive_dependents.length > 0 ? (
                      blast.transitive_dependents.map((d) => (
                        <span
                          key={d}
                          className="text-xs font-mono px-2 py-0.5 rounded bg-slate-100 text-slate-800"
                        >
                          {d}
                        </span>
                      ))
                    ) : (
                      <span className="text-xs text-slate-400 italic">None</span>
                    )}
                  </div>
                </div>

                <div className="p-3 rounded-lg bg-white border border-slate-200">
                  <div className="text-[11px] font-semibold text-slate-500 uppercase">
                    Total Affected Services
                  </div>
                  <div className="mt-1 text-2xl font-bold font-mono text-slate-900">
                    {blast.total_affected_services}
                  </div>
                  <div className="text-[11px] text-slate-500">
                    {blast.tier_0_impacted
                      ? "⚠ Includes Tier-0 mission-critical impact"
                      : "No Tier-0 core service impact"}
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Hybrid RAG Search */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-blue-600" />
            <h2 className="text-sm font-semibold text-slate-900">
              Hybrid RAG Search (BM25 + Dense + RRF)
            </h2>
          </div>
          <span className="text-xs font-mono text-slate-400">
            Live from POST /knowledge/search
          </span>
        </div>

        {/* Query Input & Filter */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="sm:col-span-2">
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Search Diagnostic Query
            </label>
            <div className="relative">
              <input
                id="rag-search-input"
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="e.g. payment heap OOM memory leak, redis connection exhaustion..."
                className="w-full text-xs font-mono rounded-lg border-slate-300 border pl-8 pr-3 py-2 text-slate-900 focus:ring-1 focus:ring-slate-900 focus:border-slate-900"
              />
              <Search className="w-4 h-4 text-slate-400 absolute left-2.5 top-2.5" />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Service Scope Filter
            </label>
            <select
              id="rag-filter-service"
              value={filterService}
              onChange={(e) => setFilterService(e.target.value)}
              className="w-full text-xs font-mono rounded-lg border-slate-300 border px-3 py-2 bg-white text-slate-900"
            >
              <option value="">All Services (Unfiltered)</option>
              {(nodes || []).map((node) => (
                <option key={node.id} value={node.id}>
                  {node.id}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* RAG Results */}
        <div className="space-y-3 pt-2">
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wide flex items-center gap-2">
            Ranked Knowledge Citations ({(ragResults || []).length} matches)
            {ragLoading && <Loader2 className="w-3.5 h-3.5 animate-spin text-slate-400" />}
          </div>

          {ragError && (
            <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              {ragError}
            </div>
          )}

          <div className="space-y-2">
            {(ragResults || []).map((r, idx) => (
              <div
                key={r.doc_id}
                className="border border-slate-200 rounded-lg p-3.5 bg-slate-50/50 hover:bg-slate-50 transition-colors space-y-2"
              >
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold font-mono px-2 py-0.5 rounded bg-slate-900 text-white">
                      #{idx + 1}
                    </span>
                    <span className="text-xs font-bold font-mono text-slate-800">{r.doc_id}</span>
                    <span className="text-xs font-medium text-slate-900">{r.title}</span>
                  </div>

                  <div className="flex items-center gap-2 text-[11px] font-mono">
                    <span className="px-2 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-200 font-bold">
                      RRF: {r.rrf_score}
                    </span>
                    <span className="px-2 py-0.5 rounded bg-slate-100 text-slate-600">
                      BM25: {r.bm25_score}
                    </span>
                    <span className="px-2 py-0.5 rounded bg-slate-100 text-slate-600">
                      Dense: {r.dense_score}
                    </span>
                  </div>
                </div>

                <p className="text-xs text-slate-600 font-sans leading-relaxed">
                  {r.citation_snippet}
                </p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
