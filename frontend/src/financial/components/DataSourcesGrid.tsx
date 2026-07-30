"use client";

import { CheckCircle2, Clock } from "lucide-react";

export interface DataSource {
  name: string;
  type: string;
  status: string;
  active_data: boolean;
  details: string;
  last_sync: string;
}

interface DataSourcesGridProps {
  sources: DataSource[];
}

export function DataSourcesGrid({ sources }: DataSourcesGridProps) {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-xl font-black text-zinc-900">Connected Data Sources & Pipelines</h3>
        <p className="text-xs text-zinc-600 font-medium mt-1">
          Active source inventory for ZÖ Agency reconciliation engine. Only configured accounts present active data.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {sources.map((source) => {
          const isConnected = source.active_data;

          return (
            <div
              key={source.name}
              className={`rounded-2xl border p-6 transition-all shadow-sm ${
                isConnected
                  ? "border-emerald-300 bg-white ring-2 ring-emerald-500/10 shadow-md"
                  : "border-zinc-200 bg-white/80"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-extrabold uppercase tracking-wider text-zinc-500">
                  {source.type}
                </span>
                {isConnected ? (
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-bold text-emerald-800 border border-emerald-300">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                    Connected & Ingesting
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-bold text-amber-800 border border-amber-300">
                    Pending Phase 2
                  </span>
                )}
              </div>

              <h4 className="text-lg font-black text-zinc-900 mt-4">{source.name}</h4>
              <p className="text-xs text-zinc-600 font-medium mt-1.5 leading-relaxed">{source.details}</p>

              <div className="mt-5 pt-4 border-t border-zinc-100 flex items-center justify-between text-xs">
                <span className="text-zinc-500 font-medium">Status:</span>
                {isConnected ? (
                  <span className="inline-flex items-center gap-1.5 font-bold text-emerald-700">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    Ingestion Active
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 font-bold text-amber-700">
                    <Clock className="h-3.5 w-3.5" />
                    Integration Scheduled
                  </span>
                )}
              </div>

              {!isConnected && (
                <div className="mt-4 rounded-xl bg-zinc-50 p-2.5 text-[11px] text-zinc-500 border border-zinc-200 text-center font-mono font-medium">
                  No dummy data loaded (Enforced)
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
