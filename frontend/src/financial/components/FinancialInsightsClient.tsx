"use client";

import { useState, useEffect, useCallback } from "react";
import { FinancialHeader } from "./FinancialHeader";
import {
  FINANCIAL_TABS,
  FinancialNavSidebar,
  type FinancialTabId,
} from "./FinancialNavSidebar";
import { TabFade } from "./TabFade";
import { IWorkerTimesheetsTable, TimesheetEntry } from "./IWorkerTimesheetsTable";
import { AiInsightsPanel, AiInsightsData } from "./AiInsightsPanel";
import { AuditQueueTable, AuditItem } from "./AuditQueueTable";
import { DataSourcesGrid, DataSource } from "./DataSourcesGrid";
import { QuickBooksPanels } from "./QuickBooksPanels";
import { TeamworkPanels } from "./TeamworkPanels";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

export function FinancialInsightsClient() {
  const [activeTab, setActiveTab] = useState<FinancialTabId>("quickbooks");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [loading, setLoading] = useState<boolean>(true);
  const [selectedContractor, setSelectedContractor] = useState<string>("all");

  // Data states
  const [iworkerData, setIworkerData] = useState<{
    contractor: string;
    source: string;
    tabs?: Array<{
      name: string;
      rate: number;
      total_hours: number;
      total_spend: number;
      active_entries: number;
    }>;
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
  const [aiInsights, setAiInsights] = useState<AiInsightsData | null>(null);

  const [isContractorLoading, setIsContractorLoading] = useState<boolean>(false);

  const fetchIworkerData = useCallback(async (contractorName: string = "all") => {
    setIsContractorLoading(true);
    try {
      const savedUrl = localStorage.getItem("zo_iworker_sheet_url") || "";
      const queryParams = new URLSearchParams();
      if (savedUrl) queryParams.set("sheet_url", savedUrl);
      if (contractorName && contractorName !== "all") queryParams.set("contractor", contractorName);

      const res = await fetch(`${API_BASE}/api/v1/financials/iworker-timesheets?${queryParams.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setIworkerData(data);
      }
    } catch (err) {
      console.error("Failed to fetch iWorker data:", err);
    } finally {
      setIsContractorLoading(false);
    }
  }, []);

  useEffect(() => {
    async function loadInitialData() {
      setLoading(true);
      try {
        const [srcRes, audRes] = await Promise.all([
          fetch(`${API_BASE}/api/v1/financials/sources`),
          fetch(`${API_BASE}/api/v1/financials/audit-queue`),
        ]);

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

    loadInitialData();
  }, []);

  // iWorker classification is expensive; wait until that tab is opened.
  useEffect(() => {
    if (activeTab !== "iworker" || iworkerData) return;
    void fetchIworkerData(selectedContractor);
  }, [activeTab, fetchIworkerData, iworkerData, selectedContractor]);

  const handleSelectContractor = (contractorName: string) => {
    setSelectedContractor(contractorName);
    void fetchIworkerData(contractorName);
  };

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

  let iworkerPanel = null;
  if (isContractorLoading && !iworkerData) {
    iworkerPanel = (
      <div className="flex h-96 w-full items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="h-10 w-10 animate-spin rounded-full border-4 border-[#3C5A56] border-t-transparent"></div>
          <p className="text-xs text-zo-text-muted font-medium animate-pulse">Loading iWorker timesheets...</p>
        </div>
      </div>
    );
  } else if (iworkerData) {
    iworkerPanel = (
      <IWorkerTimesheetsTable
        contractor={iworkerData.contractor}
        source={iworkerData.source}
        tabs={iworkerData.tabs}
        selectedContractor={selectedContractor}
        onSelectContractor={handleSelectContractor}
        isLoadingContractor={isContractorLoading}
        summary={iworkerData.summary}
        weeklyTotals={iworkerData.weekly_totals}
        timesheets={iworkerData.timesheets}
      />
    );
  }

  const activeNav = FINANCIAL_TABS.find((tab) => tab.id === activeTab) ?? FINANCIAL_TABS[0];

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1">
      <FinancialNavSidebar
        activeTab={activeTab}
        onChange={setActiveTab}
        mobileOpen={mobileNavOpen}
        onMobileOpenChange={setMobileNavOpen}
      />

      <div className="mx-auto flex min-h-0 min-w-0 w-full max-w-[1600px] flex-1 flex-col gap-3 px-5 pt-3 pb-3 sm:px-6 md:px-8">
        <FinancialHeader
          title={activeNav.label}
          subtitle={activeNav.hint}
          onOpenNav={() => setMobileNavOpen(true)}
        />

        {loading ? (
          <div className="flex min-h-0 flex-1 items-center justify-center">
            <div className="flex flex-col items-center gap-3">
              <div className="h-10 w-10 animate-spin rounded-full border-4 border-[#3C5A56] border-t-transparent" />
              <p className="text-xs font-medium text-zo-text-muted animate-pulse">Loading Financial Insights...</p>
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            {activeTab === "quickbooks" ? (
              <TabFade
                active
                className="min-h-0 flex-1 overflow-clip"
                id="financial-panel-quickbooks"
              >
                <QuickBooksPanels />
              </TabFade>
            ) : null}

            {activeTab === "teamwork" ? (
              <TabFade
                active
                className="min-h-0 flex-1 overflow-clip"
                id="financial-panel-teamwork"
              >
                <TeamworkPanels />
              </TabFade>
            ) : null}

            <TabFade
              active={activeTab === "iworker"}
              className="min-h-0 flex-1 overflow-auto"
              id="financial-panel-iworker"
            >
              {iworkerPanel}
            </TabFade>

            <TabFade
              active={activeTab === "ai"}
              className="min-h-0 flex-1 overflow-auto"
              id="financial-panel-ai"
            >
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
            </TabFade>

            <TabFade
              active={activeTab === "sources"}
              className="min-h-0 flex-1 overflow-auto"
              id="financial-panel-sources"
            >
              <DataSourcesGrid sources={sourcesData} />
            </TabFade>
          </div>
        )}
      </div>
    </div>
  );
}
