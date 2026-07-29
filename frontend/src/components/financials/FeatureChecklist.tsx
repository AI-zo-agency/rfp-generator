"use client";

import { useState } from "react";

export interface ChecklistItem {
  id: number;
  feature: string;
  phase: number;
  phase_name: string;
  status: "Pending" | "In Progress" | "Completed" | "Blocked" | string;
  deliverable: string;
  description: string;
  business_value: string;
}

export interface PhaseInfo {
  phase: number;
  name: string;
  duration: string;
  focus: string;
}

interface FeatureChecklistProps {
  initialChecklist: ChecklistItem[];
  phases: PhaseInfo[];
  onStatusChange?: (id: number, newStatus: string) => void;
}

const STATUS_CYCLE: Array<"Pending" | "In Progress" | "Completed" | "Blocked"> = [
  "Pending",
  "In Progress",
  "Completed",
  "Blocked",
];

const STATUS_CONFIG: Record<
  string,
  { label: string; icon: string; badgeStyle: string }
> = {
  Pending: {
    label: "Pending",
    icon: "📅",
    badgeStyle: "bg-zinc-100 text-foreground border-zo-border hover:bg-zinc-200",
  },
  "In Progress": {
    label: "In Progress",
    icon: "⏳",
    badgeStyle: "bg-amber-100 text-amber-800 border-amber-300 hover:bg-amber-200",
  },
  Completed: {
    label: "Completed",
    icon: "✅",
    badgeStyle: "bg-emerald-100 text-emerald-800 border-emerald-300 hover:bg-emerald-200",
  },
  Blocked: {
    label: "Blocked",
    icon: "🚫",
    badgeStyle: "bg-red-100 text-red-800 border-red-300 hover:bg-red-200",
  },
};

export function FeatureChecklist({
  initialChecklist,
  phases,
  onStatusChange,
}: FeatureChecklistProps) {
  const [items, setItems] = useState<ChecklistItem[]>(initialChecklist);
  const [selectedPhaseTab, setSelectedPhaseTab] = useState<number | "ALL">("ALL");

  const handleBadgeClick = (id: number) => {
    setItems((prevItems) =>
      prevItems.map((item) => {
        if (item.id === id) {
          const currentIndex = STATUS_CYCLE.indexOf(item.status as any);
          const nextStatus =
            STATUS_CYCLE[(currentIndex + 1) % STATUS_CYCLE.length];
          
          if (onStatusChange) {
            onStatusChange(id, nextStatus);
          }
          return { ...item, status: nextStatus };
        }
        return item;
      })
    );
  };

  const completedCount = items.filter((i) => i.status === "Completed").length;
  const totalCount = items.length;
  const progressPercent =
    totalCount > 0 ? Math.round((completedCount / totalCount) * 100) : 0;

  const filteredItems = items.filter((item) => {
    if (selectedPhaseTab === "ALL") return true;
    return item.phase === selectedPhaseTab;
  });

  return (
    <div className="space-y-8">
      {/* Development Timeline Phase Overview */}
      <div>
        <h3 className="font-heading text-xl font-bold text-foreground mb-1">Development Timeline: 6 Weeks</h3>
        <p className="text-xs text-zo-text-secondary mb-4 font-medium">
          Structured 5-phase rollout strategy for zö agency financial architecture.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
          {phases.map((p) => {
            const phaseItems = items.filter((i) => i.phase === p.phase);
            const phaseCompleted = phaseItems.filter((i) => i.status === "Completed").length;
            const phasePercent =
              phaseItems.length > 0
                ? Math.round((phaseCompleted / phaseItems.length) * 100)
                : 0;

            return (
              <div
                key={p.phase}
                onClick={() =>
                  setSelectedPhaseTab(selectedPhaseTab === p.phase ? "ALL" : p.phase)
                }
                className={`cursor-pointer rounded-2xl border p-4 transition-all shadow-sm ${
                  selectedPhaseTab === p.phase
                    ? "border-[#ef5018] bg-white ring-2 ring-[#ef5018]/20 shadow-md"
                    : "border-zo-border bg-white hover:border-zinc-300 hover:shadow-md"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-[10px] uppercase font-cabin font-extrabold tracking-wider text-[#ef5018]">
                    Phase {p.phase}
                  </span>
                  <span className="text-[10px] font-semibold text-zo-text-muted">{p.duration}</span>
                </div>
                <h4 className="font-heading text-sm font-bold text-foreground mt-1.5 line-clamp-1">{p.name}</h4>
                <p className="text-[11px] text-zo-text-secondary mt-1 line-clamp-2 leading-relaxed">{p.focus}</p>

                <div className="mt-3 pt-2 border-t border-zo-border/60 flex items-center justify-between text-[11px]">
                  <span className="text-zo-text-muted font-medium">{phaseCompleted}/{phaseItems.length} Done</span>
                  <span className="font-bold text-foreground">{phasePercent}%</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Feature Checklist Main Card */}
      <div className="rounded-2xl border border-zo-border bg-white p-6 shadow-sm">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-6 border-b border-zo-border">
          <div>
            <div className="flex items-center gap-3">
              <h3 className="font-heading text-xl font-bold text-foreground">Feature Checklist (All Phases Combined)</h3>
              <span className="rounded-full bg-[#ef5018]/10 px-3 py-1 text-xs font-bold text-[#ef5018] border border-[#ef5018]/20 font-cabin">
                {progressPercent}% Complete
              </span>
            </div>
            <p className="text-xs text-zo-text-secondary mt-1 font-medium">
              {completedCount} of {totalCount} done • Click any status badge below to cycle locally.
            </p>
          </div>

          {/* Progress Bar */}
          <div className="w-full sm:w-64">
            <div className="flex justify-between text-xs text-zo-text-secondary mb-1.5 font-bold font-cabin">
              <span>Overall Progress</span>
              <span className="text-foreground">{progressPercent}%</span>
            </div>
            <div className="h-2.5 w-full rounded-full bg-zinc-100 overflow-hidden border border-zo-border">
              <div
                className="h-full bg-gradient-to-r from-[#ef5018] to-emerald-500 transition-all duration-500"
                style={{ width: `${progressPercent}%` }}
              ></div>
            </div>
          </div>
        </div>

        {/* Phase Filter Tabs */}
        <div className="mt-6 flex flex-wrap items-center gap-2 font-cabin">
          <button
            onClick={() => setSelectedPhaseTab("ALL")}
            className={`rounded-xl px-3.5 py-1.5 text-xs font-bold transition-all ${
              selectedPhaseTab === "ALL"
                ? "bg-[#ef5018] text-white shadow-md shadow-[#ef5018]/20"
                : "bg-zinc-100 text-zo-text-secondary hover:text-foreground hover:bg-zinc-200 border border-zo-border"
            }`}
          >
            All Phases ({items.length})
          </button>
          {[1, 2, 3, 4, 5].map((ph) => (
            <button
              key={ph}
              onClick={() => setSelectedPhaseTab(ph)}
              className={`rounded-xl px-3.5 py-1.5 text-xs font-bold transition-all ${
                selectedPhaseTab === ph
                  ? "bg-[#ef5018] text-white shadow-md shadow-[#ef5018]/20"
                  : "bg-zinc-100 text-zo-text-secondary hover:text-foreground hover:bg-zinc-200 border border-zo-border"
              }`}
            >
              Phase {ph}
            </button>
          ))}
        </div>

        {/* Checklist Table */}
        <div className="mt-6 overflow-hidden rounded-2xl border border-zo-border bg-white">
          <table className="w-full text-left text-xs">
            <thead className="bg-[#0c0c0e] text-white uppercase font-cabin tracking-wider font-bold text-[10px]">
              <tr>
                <th className="px-4 py-4 w-12 text-center">#</th>
                <th className="px-5 py-4">Feature & Deliverable</th>
                <th className="px-5 py-4">Phase</th>
                <th className="px-6 py-4">Description</th>
                <th className="px-6 py-4">Business Value</th>
                <th className="px-5 py-4 text-center">Status (Click to cycle)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zo-border font-normal bg-white">
              {filteredItems.map((item) => {
                const conf = STATUS_CONFIG[item.status] || STATUS_CONFIG["Pending"];

                return (
                  <tr key={item.id} className="hover:bg-zinc-50 transition-colors">
                    <td className="px-4 py-4 text-center font-mono font-bold text-zo-text-muted">
                      {item.id}
                    </td>
                    <td className="px-5 py-4 font-bold text-foreground">
                      <div>{item.feature}</div>
                      <div className="text-[10px] text-zo-text-muted font-medium">{item.deliverable}</div>
                    </td>
                    <td className="px-5 py-4 whitespace-nowrap">
                      <span className="inline-block rounded-md bg-zinc-100 px-2 py-1 text-[11px] font-bold text-foreground border border-zo-border">
                        Phase {item.phase}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-zo-text-secondary font-medium max-w-xs leading-relaxed">
                      {item.description}
                    </td>
                    <td className="px-6 py-4 text-emerald-700 font-medium max-w-xs leading-relaxed">
                      {item.business_value}
                    </td>
                    <td className="px-5 py-4 text-center whitespace-nowrap">
                      <button
                        onClick={() => handleBadgeClick(item.id)}
                        className={`inline-flex items-center gap-1.5 rounded-full px-3.5 py-1 text-xs font-cabin font-bold border transition-all cursor-pointer shadow-sm active:scale-95 ${conf.badgeStyle}`}
                        title="Click to cycle status"
                      >
                        <span>{conf.icon}</span>
                        <span>{conf.label}</span>
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
