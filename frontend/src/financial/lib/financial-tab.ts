export const FINANCIAL_TAB_IDS = [
  "agency",
  "quickbooks",
  "teamwork",
  "iworker",
  "sources",
] as const;

export type FinancialTabId = (typeof FINANCIAL_TAB_IDS)[number];

export const AGENCY_JOBS_VIEW_IDS = ["jobs", "portfolio", "invoices", "orphans"] as const;

export type AgencyJobsViewId = (typeof AGENCY_JOBS_VIEW_IDS)[number];

export const AGENCY_VIEW_IDS = [...AGENCY_JOBS_VIEW_IDS, "mapping"] as const;

export type AgencyViewId = (typeof AGENCY_VIEW_IDS)[number];

export const TEAMWORK_VIEW_IDS = ["position", "projects", "work", "time"] as const;

export type TeamworkViewId = (typeof TEAMWORK_VIEW_IDS)[number];

export function parseFinancialTab(raw: string | null | undefined): FinancialTabId {
  if (raw === "ai") return "iworker";
  return FINANCIAL_TAB_IDS.includes(raw as FinancialTabId) ? (raw as FinancialTabId) : "quickbooks";
}

export function parseAgencyView(raw: string | null | undefined): AgencyViewId {
  return AGENCY_VIEW_IDS.includes(raw as AgencyViewId) ? (raw as AgencyViewId) : "jobs";
}

export function parseTeamworkView(raw: string | null | undefined): TeamworkViewId {
  return TEAMWORK_VIEW_IDS.includes(raw as TeamworkViewId) ? (raw as TeamworkViewId) : "position";
}

export function applyFinancialNavSearch(
  currentSearch: string,
  patch: { tab?: FinancialTabId; view?: AgencyViewId | TeamworkViewId },
): string {
  const params = new URLSearchParams(currentSearch.replace(/^\?/, ""));
  if (patch.tab !== undefined) {
    if (patch.tab === "quickbooks") params.delete("tab");
    else params.set("tab", patch.tab);
    const currentView = params.get("view");
    if (
      (patch.tab === "agency" && !AGENCY_VIEW_IDS.includes(currentView as AgencyViewId)) ||
      (patch.tab === "teamwork" && !TEAMWORK_VIEW_IDS.includes(currentView as TeamworkViewId)) ||
      (patch.tab !== "agency" && patch.tab !== "teamwork")
    ) {
      params.delete("view");
    }
  }
  if (patch.view !== undefined) {
    if (patch.view === "jobs" || patch.view === "position") params.delete("view");
    else params.set("view", patch.view);
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}
