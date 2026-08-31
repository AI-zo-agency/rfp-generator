"use client";

import { useState } from "react";
import { AuditQueueTable, type AuditItem } from "./AuditQueueTable";
import {
  AiIntelligenceDrawer,
  type DrawerChrome,
} from "./AiIntelligenceDrawer";
import { IWorkerTimesheetsTable } from "./IWorkerTimesheetsTable";
import type { IWorkerTimesheetsResponse, PeriodGranularity } from "../types/iworker";
import { useIworkerInsights } from "../lib/use-iworker-insights";
import type { QbChat } from "../lib/use-qb-chat";
import "./AiIntelligenceDrawer.css";

const IWORKER_CHROME: DrawerChrome = {
  source: "iWorker",
  seeds: [
    "Which contractor is under-logged?",
    "What scope risks stand out this week?",
    "Summarize spend vs last week",
  ],
  viewLabel: {},
  placeholder: "Ask about contractor logs…",
  empty: "No contractor flags for this period. Timesheets look clean.",
};

const IW_CHAT_STUB: QbChat = {
  turns: [],
  busy: false,
  costUsd: 0,
  send: async () => {},
  reset: () => {},
};

export interface IWorkerPanelsProps {
  iworkerData: IWorkerTimesheetsResponse | null;
  isLoading: boolean;
  selectedContractor: string;
  onSelectContractor: (name: string) => void;
  granularity: PeriodGranularity;
  onGranularityChange: (g: PeriodGranularity) => void;
  onPeriodStartChange: (iso: string | null) => void;
  periodFilterEnabled: boolean;
  onTogglePeriodFilter: () => void;
  periodStart: string | null;
  auditItems: AuditItem[];
  onResolveAuditItem: (id: string, action: string) => Promise<void>;
}

export function IWorkerPanels({
  iworkerData,
  isLoading,
  selectedContractor,
  onSelectContractor,
  granularity,
  onGranularityChange,
  onPeriodStartChange,
  periodFilterEnabled,
  onTogglePeriodFilter,
  periodStart,
  auditItems,
  onResolveAuditItem,
}: IWorkerPanelsProps) {
  const [aiOpen, setAiOpen] = useState(false);

  const insights = useIworkerInsights(
    iworkerData?.period_insights,
    auditItems,
    granularity,
    periodStart,
  );

  if (isLoading && !iworkerData) {
    return (
      <div className="flex h-96 w-full items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="h-10 w-10 animate-spin rounded-full border-4 border-[#3C5A56] border-t-transparent" />
          <p className="text-xs text-zo-text-muted font-medium animate-pulse">
            Loading iWorker timesheets...
          </p>
        </div>
      </div>
    );
  }

  if (!iworkerData) return null;

  return (
    <>
      <IWorkerTimesheetsTable
        contractor={iworkerData.contractor}
        source={iworkerData.source}
        tabs={iworkerData.tabs}
        selectedContractor={selectedContractor}
        onSelectContractor={onSelectContractor}
        isLoadingContractor={isLoading}
        summary={iworkerData.summary}
        timesheets={iworkerData.timesheets}
        periodInsights={iworkerData.period_insights}
        periodHistory={iworkerData.period_history}
        granularity={granularity}
        onGranularityChange={onGranularityChange}
        onPeriodStartChange={onPeriodStartChange}
        periodFilterEnabled={periodFilterEnabled}
        onTogglePeriodFilter={onTogglePeriodFilter}
        aiHighImpact={insights.highImpact}
        aiOpen={aiOpen}
        onOpenAi={() => setAiOpen(true)}
      />

      <AiIntelligenceDrawer
        open={aiOpen}
        onClose={() => setAiOpen(false)}
        insights={insights}
        chat={IW_CHAT_STUB}
        onGo={() => setAiOpen(false)}
        chrome={IWORKER_CHROME}
        showChat={false}
        feedExtra={
          auditItems.length > 0 ? (
            <div className="qb-ai-audit-queue mt-6 space-y-3 border-t border-zinc-200 pt-5">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-zo-text-muted">
                Audit queue
              </p>
              <AuditQueueTable
                initialAuditItems={auditItems}
                onResolveItem={onResolveAuditItem}
              />
            </div>
          ) : null
        }
      />
    </>
  );
}
