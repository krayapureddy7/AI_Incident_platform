import React from "react";
import {
  ShieldAlert,
  Activity,
  Network,
  CheckCircle2,
  Terminal,
  FileText,
  SlidersHorizontal,
} from "lucide-react";

export type NavTab =
  | "studio"
  | "evals"
  | "knowledge"
  | "safety"
  | "tools"
  | "docs";

interface NavbarProps {
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
}

export const Navbar: React.FC<NavbarProps> = ({ activeTab, onTabChange }) => {
  const tabs: Array<{ id: NavTab; label: string; icon: React.ReactNode }> = [
    { id: "studio", label: "Incident Studio", icon: <Activity className="w-4 h-4" /> },
    { id: "evals", label: "Evaluation Suite", icon: <CheckCircle2 className="w-4 h-4" /> },
    { id: "knowledge", label: "Knowledge & RAG", icon: <Network className="w-4 h-4" /> },
    { id: "safety", label: "Safety & Guardrails", icon: <ShieldAlert className="w-4 h-4" /> },
    { id: "tools", label: "MCP Tools", icon: <Terminal className="w-4 h-4" /> },
    { id: "docs", label: "Architecture & Slides", icon: <FileText className="w-4 h-4" /> },
  ];

  return (
    <header className="border-b border-slate-200 bg-white sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          {/* Logo & System Identity */}
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-slate-900 flex items-center justify-center text-white shadow-sm">
              <ShieldAlert className="w-5 h-5 text-emerald-400" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-semibold text-slate-900 tracking-tight">
                  Mini Agentic AI Platform
                </span>
                <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-medium bg-slate-100 text-slate-700 border border-slate-300">
                  v1.2.0
                </span>
              </div>
              <p className="text-xs text-slate-500 font-mono">
                Protocol: MCP/2024-11-05-local • 4 Agents • FSM Orchestrator
              </p>
            </div>
          </div>

          {/* System Status Indicators */}
          <div className="hidden lg:flex items-center gap-3">
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-emerald-50 text-emerald-700 text-xs font-medium border border-emerald-200">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              Evals: 3/3 Passed (100%)
            </div>
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-slate-50 text-slate-700 text-xs font-medium border border-slate-200">
              <span className="w-2 h-2 rounded-full bg-blue-500" />
              22 Unit Tests Green
            </div>
          </div>
        </div>

        {/* Navigation Tabs */}
        <nav className="flex space-x-1 overflow-x-auto no-scrollbar -mb-px border-t border-slate-100 pt-1">
          {tabs.map((tab) => {
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                id={`nav-tab-${tab.id}`}
                onClick={() => onTabChange(tab.id)}
                className={`flex items-center gap-2 px-3.5 py-2.5 text-xs sm:text-sm font-medium border-b-2 whitespace-nowrap transition-colors ${
                  isActive
                    ? "border-slate-900 text-slate-900 bg-slate-50/50"
                    : "border-transparent text-slate-600 hover:text-slate-900 hover:border-slate-300"
                }`}
              >
                {tab.icon}
                {tab.label}
              </button>
            );
          })}
        </nav>
      </div>
    </header>
  );
};
