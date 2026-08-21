export const FINANCIAL_TAB_IDS = [
  "quickbooks",
  "teamwork",
  "iworker",
  "ai",
  "sources",
] as const;

export type FinancialTabId = (typeof FINANCIAL_TAB_IDS)[number];

export const TEAMWORK_VIEW_IDS = ["position", "projects", "work", "time"] as const;

export type TeamworkViewId = (typeof TEAMWORK_VIEW_IDS)[number];

export function parseFinancialTab(raw: string | null | undefined): FinancialTabId {
  return FINANCIAL_TAB_IDS.includes(raw as FinancialTabId) ? (raw as FinancialTabId) : "quickbooks";
}

export function parseTeamworkView(raw: string | null | undefined): TeamworkViewId {
  return TEAMWORK_VIEW_IDS.includes(raw as TeamworkViewId) ? (raw as TeamworkViewId) : "position";
}

export function applyFinancialNavSearch(
  currentSearch: string,
  patch: { tab?: FinancialTabId; view?: TeamworkViewId },
): string {
  const params = new URLSearchParams(currentSearch.replace(/^\?/, ""));
  if (patch.tab !== undefined) {
    if (patch.tab === "quickbooks") params.delete("tab");
    else params.set("tab", patch.tab);
    if (patch.tab !== "teamwork") params.delete("view");
  }
  if (patch.view !== undefined) {
    if (patch.view === "position") params.delete("view");
    else params.set("view", patch.view);
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}
