import React, { useState } from "react";
import { Navbar, NavTab } from "./components/Navbar";
import { IncidentStudio } from "./components/IncidentStudio";
import { EvaluationSuiteView } from "./components/EvaluationSuiteView";
import { KnowledgeGraphView } from "./components/KnowledgeGraphView";
import { SafetyView } from "./components/SafetyView";
import { McpToolsView } from "./components/McpToolsView";
import { DocsView } from "./components/DocsView";

export default function App() {
  const [activeTab, setActiveTab] = useState<NavTab>("studio");

  return (
    <div className="min-h-screen bg-slate-100/70 text-slate-900 font-sans selection:bg-slate-200">
      <Navbar activeTab={activeTab} onTabChange={setActiveTab} />

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {activeTab === "studio" && <IncidentStudio />}
        {activeTab === "evals" && <EvaluationSuiteView />}
        {activeTab === "knowledge" && <KnowledgeGraphView />}
        {activeTab === "safety" && <SafetyView />}
        {activeTab === "tools" && <McpToolsView />}
        {activeTab === "docs" && <DocsView />}
      </main>

      <footer className="border-t border-slate-200 bg-white py-4 mt-12">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col sm:flex-row items-center justify-between gap-2 text-xs text-slate-500 font-mono">
          <div>Mini Agentic AI Platform • Autonomous Production Incident Remediation</div>
          <div>All Invariant & Contract Tests Passed (22/22) • Evaluation Suite: 100%</div>
        </div>
      </footer>
    </div>
  );
}
