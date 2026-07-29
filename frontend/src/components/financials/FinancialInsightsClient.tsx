"use client";

import { useState, useEffect } from "react";
import { DashboardHeader } from "@/components/DashboardHeader";
import { OutlineTabs } from "@/components/ui/OutlineTabs";
import { IWorkerTimesheetsTable, TimesheetEntry } from "./IWorkerTimesheetsTable";
import { AiInsightsPanel, AiInsightsData } from "./AiInsightsPanel";
import { AuditQueueTable, AuditItem } from "./AuditQueueTable";
import { DataSourcesGrid, DataSource } from "./DataSourcesGrid";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

const FINANCIAL_TABS = [
  { id: "iworker", label: "iWorker Ingestion & Logs" },
  { id: "ai", label: "AI Audit Queue & Insights" },
  { id: "sources", label: "Data Sources Inventory" },
];

export function FinancialInsightsClient() {
  const [activeTab, setActiveTab] = useState<string>("iworker");
  const [loading, setLoading] = useState<boolean>(true);

  // Data states
  const [iworkerData, setIworkerData] = useState<{
    contractor: string;
    source: string;
    summary: {
      total_logged_hours: number;
      total_spend_usd: number;
      active_tasks_count: number;
      hourly_rate_usd: number;
      unbilled_risk_amount: number;
    };
    weekly_totals: Array<{
      week_ending: string;
      total_hours: number;
      total_amount: number;
      entries_count: number;
    }>;
    timesheets: TimesheetEntry[];
  } | null>(null);

  const [sourcesData, setSourcesData] = useState<DataSource[]>([]);
  const [auditItems, setAuditItems] = useState<AuditItem[]>([]);

  // AI insights state lifted here so it persists across tab switches
  const [aiInsights, setAiInsights] = useState<AiInsightsData | null>(null);

  useEffect(() => {
    async function loadAllData() {
      setLoading(true);
      try {
        const savedUrl = localStorage.getItem("zo_iworker_sheet_url") || "";
        const iwQuery = savedUrl ? `?sheet_url=${encodeURIComponent(savedUrl)}` : "";
        const [iwRes, srcRes, audRes] = await Promise.all([
          fetch(`${API_BASE}/api/v1/financials/iworker-timesheets${iwQuery}`),
          fetch(`${API_BASE}/api/v1/financials/sources`),
          fetch(`${API_BASE}/api/v1/financials/audit-queue`),
        ]);

        if (iwRes.ok) setIworkerData(await iwRes.json());
        if (srcRes.ok) {
          const sJson = await srcRes.json();
          setSourcesData(sJson.sources || []);
        }
        if (audRes.ok) {
          const aJson = await audRes.json();
          setAuditItems(aJson.audit_items || []);
        }
      } catch (err: any) {
        console.error("Failed to load financials data:", err);
      } finally {
        setLoading(false);
      }
    }

    loadAllData();
  }, []);

  const handleFetchAiInsights = async (): Promise<AiInsightsData> => {
    const res = await fetch(`${API_BASE}/api/v1/financials/ai-insights`, {
      method: "POST",
    });
    if (!res.ok) {
      throw new Error("Failed to generate AI insights");
    }
    const data = await res.json();
    // Cache the result so switching tabs doesn't lose it
    setAiInsights(data);
    return data;
  };

  const handleResolveAuditItem = async (id: string, action: string) => {
    try {
      await fetch(`${API_BASE}/api/v1/financials/audit-queue/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, action }),
      });
    } catch (e) {
      console.warn("Failed to persist audit item resolution:", e);
    }
  };

  if (loading) {
    return (
      <div className="flex h-96 w-full items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="h-10 w-10 animate-spin rounded-full border-4 border-[#ef5018] border-t-transparent"></div>
          <p className="text-xs text-zo-text-muted font-medium animate-pulse">Loading Financial Insights...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-8 sm:space-y-10">
      {/* RFP Header Component */}
      <DashboardHeader
        title="Financial & Margin Auditor"
        subtitle="Reconciliation, margin tracking, and human-reviewed audit queue. Integrated with live iWorker Google Sheets timesheets."
        showSync={false}
      />

      {/* Navigation Tabs — always left-aligned */}
      <div className="flex justify-start">
        <OutlineTabs
          tabs={FINANCIAL_TABS}
          activeTab={activeTab}
          onChange={(id) => setActiveTab(id)}
        />
      </div>

      {/* Tab 1: iWorker Ingestion — always mounted, hidden when not active */}
      <div className={activeTab === "iworker" ? "block" : "hidden"}>
        {iworkerData && (
          <IWorkerTimesheetsTable
            contractor={iworkerData.contractor}
            source={iworkerData.source}
            summary={iworkerData.summary}
            weeklyTotals={iworkerData.weekly_totals}
            timesheets={iworkerData.timesheets}
          />
        )}
      </div>

      {/* Tab 2: AI Audit Queue & Insights — always mounted, hidden when not active */}
      <div className={activeTab === "ai" ? "block" : "hidden"}>
        <div className="space-y-8">
          <AiInsightsPanel
            onFetchAiInsights={handleFetchAiInsights}
            persistedInsights={aiInsights}
            onInsightsGenerated={setAiInsights}
          />
          <AuditQueueTable
            initialAuditItems={auditItems}
            onResolveItem={handleResolveAuditItem}
          />
        </div>
      </div>

      {/* Tab 3: Connected Data Sources — always mounted, hidden when not active */}
      <div className={activeTab === "sources" ? "block" : "hidden"}>
        <DataSourcesGrid sources={sourcesData} />
      </div>
    </div>
  );
}
