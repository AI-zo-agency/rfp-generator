"use client";

import { useState, useEffect, useCallback } from "react";
import { FinancialHeader } from "./FinancialHeader";
import {
  FINANCIAL_TABS,
  FinancialNavSidebar,
  type FinancialTabId,
} from "./FinancialNavSidebar";
import {
  applyFinancialNavSearch,
  type AgencyViewId,
  type TeamworkViewId,
} from "../lib/financial-tab";
import { TabFade } from "./TabFade";
import { IWorkerTimesheetsTable } from "./IWorkerTimesheetsTable";
import { AiInsightsPanel, AiInsightsData } from "./AiInsightsPanel";
import type { IWorkerTimesheetsResponse, PeriodGranularity } from "../types/iworker";
import { AuditQueueTable, AuditItem } from "./AuditQueueTable";
import { DataSourcesGrid, DataSource } from "./DataSourcesGrid";
import { QuickBooksPanels } from "./QuickBooksPanels";
import { TeamworkPanels } from "./TeamworkPanels";
import { ClientMapPanels } from "./ClientMapPanels";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

function persistFinancialNav(patch: {
  tab?: FinancialTabId;
  view?: AgencyViewId | TeamworkViewId;
}) {
  const next = applyFinancialNavSearch(window.location.search, patch);
  try {
    window.history.replaceState(null, "", `${window.location.pathname}${next}`);
  } catch (err) {
    console.warn("Failed to persist financial tab to the URL:", err);
  }
}

export function FinancialInsightsClient({
  initialTab = "quickbooks",
  initialAgencyView = "jobs",
  initialView = "position",
}: {
  initialTab?: FinancialTabId;
  initialAgencyView?: AgencyViewId;
  initialView?: TeamworkViewId;
}) {
  const [activeTab, setActiveTab] = useState<FinancialTabId>(initialTab);
  const [agencyView, setAgencyView] = useState<AgencyViewId>(initialAgencyView);
  const [teamworkView, setTeamworkView] = useState<TeamworkViewId>(initialView);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [loading, setLoading] = useState<boolean>(true);
  const [selectedContractor, setSelectedContractor] = useState<string>("all");
  const [granularity, setGranularity] = useState<PeriodGranularity>("week");
  const [periodStart, setPeriodStart] = useState<string | null>(null);
  const [periodFilterEnabled, setPeriodFilterEnabled] = useState(true);

  // Data states
  const [iworkerData, setIworkerData] = useState<IWorkerTimesheetsResponse | null>(null);

  const [sourcesData, setSourcesData] = useState<DataSource[]>([]);
  const [auditItems, setAuditItems] = useState<AuditItem[]>([]);
  const [aiInsights, setAiInsights] = useState<AiInsightsData | null>(null);

  const [isContractorLoading, setIsContractorLoading] = useState<boolean>(false);

  const fetchIworkerData = useCallback(
    async (
      contractorName: string = "all",
      opts?: { granularity?: PeriodGranularity; periodStart?: string | null },
    ) => {
      setIsContractorLoading(true);
      try {
        const savedUrl = localStorage.getItem("zo_iworker_sheet_url") || "";
        const queryParams = new URLSearchParams();
        if (savedUrl) queryParams.set("sheet_url", savedUrl);
        if (contractorName && contractorName !== "all") {
          queryParams.set("contractor", contractorName);
        }
        const g = opts?.granularity ?? granularity;
        const ps = opts?.periodStart !== undefined ? opts.periodStart : periodStart;
        queryParams.set("granularity", g);
        if (ps) queryParams.set("period_start", ps);

        const res = await fetch(
          `${API_BASE}/api/v1/financials/iworker-timesheets?${queryParams.toString()}`,
        );
        if (res.ok) {
          const data = await res.json();
          setIworkerData(data);
        }
      } catch (err) {
        console.error("Failed to fetch iWorker data:", err);
      } finally {
        setIsContractorLoading(false);
      }
    },
    [granularity, periodStart],
  );

  const fetchAuditQueue = useCallback(async () => {
    try {
      const queryParams = new URLSearchParams();
      queryParams.set("granularity", granularity);
      if (periodStart) queryParams.set("period_start", periodStart);
      const res = await fetch(
        `${API_BASE}/api/v1/financials/audit-queue?${queryParams.toString()}`,
      );
      if (res.ok) {
        const aJson = await res.json();
        setAuditItems(aJson.audit_items || []);
      }
    } catch (err) {
      console.error("Failed to fetch audit queue:", err);
    }
  }, [granularity, periodStart]);

  useEffect(() => {
    async function loadInitialData() {
      setLoading(true);
      try {
        // Sources only on mount. audit-queue derives from iWorker timesheets and
        // must not run until that cache is warm (iWorker tab open), or it would
        // re-trigger the expensive sheet + classifier pull on every reload.
        const srcRes = await fetch(`${API_BASE}/api/v1/financials/sources`);
        if (srcRes.ok) {
          const sJson = await srcRes.json();
          setSourcesData(sJson.sources || []);
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
    if (activeTab !== "iworker") return;
    void fetchIworkerData(selectedContractor);
  }, [activeTab, selectedContractor, granularity, periodStart, fetchIworkerData]);

  // Audit flags come from the timesheet cache filled by the iWorker tab.
  useEffect(() => {
    if (activeTab !== "ai") return;
    void fetchAuditQueue();
  }, [activeTab, fetchAuditQueue]);

  // After iWorker loads or period changes, refresh audit for the AI tab.
  useEffect(() => {
    if (!iworkerData) return;
    void fetchAuditQueue();
  }, [iworkerData, granularity, periodStart, fetchAuditQueue]);

  const selectTab = (id: FinancialTabId) => {
    setActiveTab(id);
    persistFinancialNav({ tab: id });
  };

  const selectTeamworkView = (id: TeamworkViewId) => {
    setTeamworkView(id);
    persistFinancialNav({ view: id });
  };

  const selectAgencyView = (id: AgencyViewId) => {
    setAgencyView(id);
    persistFinancialNav({ view: id });
  };

  const handleSelectContractor = (contractorName: string) => {
    setSelectedContractor(contractorName);
  };

  const handleGranularityChange = (g: PeriodGranularity) => {
    setGranularity(g);
    setPeriodStart(null);
  };

  const handlePeriodStartChange = (iso: string | null) => {
    setPeriodStart(iso);
  };

  const handleTogglePeriodFilter = () => {
    setPeriodFilterEnabled((prev) => !prev);
  };

  const handleFetchAiInsights = async (): Promise<AiInsightsData> => {
    const queryParams = new URLSearchParams();
    queryParams.set("granularity", granularity);
    if (periodStart) queryParams.set("period_start", periodStart);
    const res = await fetch(
      `${API_BASE}/api/v1/financials/ai-insights?${queryParams.toString()}`,
      { method: "POST" },
    );
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
        timesheets={iworkerData.timesheets}
        periodInsights={iworkerData.period_insights}
        periodHistory={iworkerData.period_history}
        granularity={granularity}
        onGranularityChange={handleGranularityChange}
        onPeriodStartChange={handlePeriodStartChange}
        periodFilterEnabled={periodFilterEnabled}
        onTogglePeriodFilter={handleTogglePeriodFilter}
      />
    );
  }

  const activeNav = FINANCIAL_TABS.find((tab) => tab.id === activeTab) ?? FINANCIAL_TABS[0];

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1">
      <FinancialNavSidebar
        activeTab={activeTab}
        onChange={selectTab}
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
            {activeTab === "agency" ? (
              <TabFade
                active
                className="min-h-0 flex-1 overflow-clip"
                id="financial-panel-agency"
              >
                <ClientMapPanels view={agencyView} onViewChange={selectAgencyView} />
              </TabFade>
            ) : null}

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
                <TeamworkPanels view={teamworkView} onViewChange={selectTeamworkView} />
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
